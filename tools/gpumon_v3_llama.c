
#define _GNU_SOURCE
#include <ncurses.h>
#include <nvml.h>
#include <curl/curl.h>
#include <json-c/json.h>
#include <systemd/sd-journal.h>

#include <errno.h>
#include <glob.h>
#include <locale.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>

#define MAX_GPUS 16
#define HIST_MAX 64
#define MAX_MODELS 8

typedef struct {
    nvmlDevice_t h;
    char name[96];

    unsigned int gpu_util;
    unsigned int mem_util;
    unsigned long long mem_total;
    unsigned long long mem_used;

    unsigned int temp;
    unsigned int memory_temp;
    unsigned int memory_max_temp;
    unsigned int power_mw;
    unsigned int power_limit_mw;
    unsigned int sm_clock;
    unsigned int mem_clock;
    nvmlPstates_t pstate;
    unsigned long long throttle_reasons;

    unsigned int pcie_gen;
    unsigned int pcie_width;
    unsigned int pcie_gen_max;
    unsigned int pcie_width_max;
    double pcie_capacity_MBps;

    double rx_MBps;
    double tx_MBps;
    double rx_peak;
    double tx_peak;

    double rx_hist[HIST_MAX];
    double tx_hist[HIST_MAX];
    int hist_pos;
    int hist_count;
} GpuStats;

typedef enum {
    PHASE_UNKNOWN = 0,
    PHASE_IDLE,
    PHASE_PREFILL,
    PHASE_GENERATING
} OllamaPhase;

typedef enum {
    BACKEND_AUTO = 0,
    BACKEND_LLAMA,
    BACKEND_OLLAMA
} BackendMode;

typedef struct {
    char name[160];
    char parameter_size[32];
    char quantization[32];
    char family[64];

    uint64_t size_bytes;
    uint64_t size_vram_bytes;
    uint64_t context_loaded;
    uint64_t context_model_max;
} OllamaModel;

typedef struct {
    int api_ok;
    char endpoint[256];
    char backend[24];
    char profile[96];
    long llama_pid;
    int unsloth;

    OllamaModel models[MAX_MODELS];
    int model_count;

    OllamaPhase phase;
    int slot_id;
    long long task_id;

    uint64_t slot_context;
    uint64_t prompt_tokens;
    uint64_t prompt_processed_tokens;
    uint64_t cached_prompt_tokens;
    uint64_t prompt_total_est;
    uint64_t last_prompt_tokens;
    double prompt_progress;
    double prompt_tps;
    double last_prompt_tps;

    uint64_t decoded_tokens;
    double gen_tps;
    double gen_tps_3s;
    double last_gen_tps;

    char kv_k[24];
    char kv_v[24];
    char kv_k_draft[24];
    char kv_v_draft[24];
    double kv_total_mib;
    double kv_k_mib;
    double kv_v_mib;

    int flash_attn; /* -1 unknown, 0 off, 1 on */
    int parallel;
    int batch_size;
    int ubatch_size;
    int spec_draft_n_max;
    int layers_model;
    int layers_offload;
    char gpu_layers[24];
    char gpu_layers_draft[24];
    char split_mode[24];
    char layers_split[128];
    char spec_type[96];

    uint64_t metric_prompt_tokens;
    uint64_t metric_cached_tokens;
    uint64_t metric_predicted_tokens;
    uint64_t metric_draft_tokens;
    uint64_t metric_accepted_tokens;
    double mtp_acceptance_pct;
    int requests_processing;
    int requests_deferred;

    char last_event[160];
    struct timespec last_activity;
} OllamaStats;

typedef struct {
    char *data;
    size_t len;
} HttpBuf;

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static double bytes_to_gib(uint64_t b) {
    return (double)b / 1024.0 / 1024.0 / 1024.0;
}

/* Approximate maximum payload bandwidth per lane, per direction. */
static double pcie_lane_MBps(unsigned int gen) {
    switch (gen) {
        case 1: return 250.0;       /* 2.5 GT/s, 8b/10b */
        case 2: return 500.0;       /* 5.0 GT/s, 8b/10b */
        case 3: return 984.615;     /* 8.0 GT/s, 128b/130b */
        case 4: return 1969.231;    /* 16 GT/s */
        case 5: return 3938.462;    /* 32 GT/s */
        case 6: return 7562.5;      /* useful approximation for PCIe 6.0 */
        default: return 0.0;
    }
}

static double clamp_pct(double x) {
    if (x < 0.0) return 0.0;
    if (x > 999.0) return 999.0;
    return x;
}

static double avg_hist(const double *a, int count) {
    if (count <= 0) return 0.0;
    double s = 0.0;
    for (int i = 0; i < count; i++) s += a[i];
    return s / (double)count;
}

static size_t http_write_cb(void *ptr, size_t size, size_t nmemb, void *userdata) {
    size_t n = size * nmemb;
    HttpBuf *b = (HttpBuf *)userdata;
    char *p = realloc(b->data, b->len + n + 1);
    if (!p) return 0;
    b->data = p;
    memcpy(b->data + b->len, ptr, n);
    b->len += n;
    b->data[b->len] = '\0';
    return n;
}

static char *http_request(const char *url, const char *post_json) {
    CURL *curl = curl_easy_init();
    if (!curl) return NULL;

    HttpBuf b = {0};
    struct curl_slist *headers = NULL;

    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, http_write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &b);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 300L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 700L);
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);

    if (post_json) {
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_json);
    }

    CURLcode rc = curl_easy_perform(curl);
    long status = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);

    if (headers) curl_slist_free_all(headers);
    curl_easy_cleanup(curl);

    if (rc != CURLE_OK || status < 200 || status >= 300) {
        free(b.data);
        return NULL;
    }
    return b.data;
}

static void json_copy_string(json_object *obj, const char *key, char *dst, size_t dstsz) {
    json_object *v = NULL;
    if (json_object_object_get_ex(obj, key, &v) && json_object_is_type(v, json_type_string)) {
        snprintf(dst, dstsz, "%s", json_object_get_string(v));
    }
}

static uint64_t json_u64(json_object *obj, const char *key) {
    json_object *v = NULL;
    if (json_object_object_get_ex(obj, key, &v)) {
        return (uint64_t)json_object_get_int64(v);
    }
    return 0;
}

static uint64_t extract_model_max_context(json_object *root) {
    json_object *mi = NULL;
    if (!json_object_object_get_ex(root, "model_info", &mi) ||
        !json_object_is_type(mi, json_type_object)) {
        return 0;
    }

    char arch[96] = {0};
    json_object *a = NULL;
    if (json_object_object_get_ex(mi, "general.architecture", &a) &&
        json_object_is_type(a, json_type_string)) {
        snprintf(arch, sizeof(arch), "%s", json_object_get_string(a));
    }

    if (arch[0]) {
        char wanted[128];
        snprintf(wanted, sizeof(wanted), "%s.context_length", arch);
        json_object *v = NULL;
        if (json_object_object_get_ex(mi, wanted, &v)) {
            return (uint64_t)json_object_get_int64(v);
        }
    }

    uint64_t best = 0;
    json_object_object_foreach(mi, key, val) {
        size_t kl = strlen(key);
        const char *suffix = ".context_length";
        size_t sl = strlen(suffix);
        if (kl >= sl && strcmp(key + kl - sl, suffix) == 0) {
            uint64_t x = (uint64_t)json_object_get_int64(val);
            if (x > best) best = x;
        }
    }
    return best;
}

static void ollama_fetch_show(OllamaStats *o, OllamaModel *m) {
    if (!m->name[0]) return;

    char url[512];
    snprintf(url, sizeof(url), "%s/api/show", o->endpoint);

    json_object *req = json_object_new_object();
    json_object_object_add(req, "model", json_object_new_string(m->name));
    const char *reqs = json_object_to_json_string_ext(req, JSON_C_TO_STRING_PLAIN);

    char *body = http_request(url, reqs);
    json_object_put(req);
    if (!body) return;

    json_object *root = json_tokener_parse(body);
    free(body);
    if (!root) return;

    m->context_model_max = extract_model_max_context(root);

    json_object *details = NULL;
    if (json_object_object_get_ex(root, "details", &details)) {
        if (!m->parameter_size[0])
            json_copy_string(details, "parameter_size", m->parameter_size, sizeof(m->parameter_size));
        if (!m->quantization[0])
            json_copy_string(details, "quantization_level", m->quantization, sizeof(m->quantization));
        if (!m->family[0])
            json_copy_string(details, "family", m->family, sizeof(m->family));
    }

    json_object_put(root);
}

static void ollama_fetch_ps(OllamaStats *o) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/ps", o->endpoint);

    char *body = http_request(url, NULL);
    if (!body) {
        o->api_ok = 0;
        return;
    }

    json_object *root = json_tokener_parse(body);
    free(body);
    if (!root) {
        o->api_ok = 0;
        return;
    }

    json_object *arr = NULL;
    if (!json_object_object_get_ex(root, "models", &arr) ||
        !json_object_is_type(arr, json_type_array)) {
        json_object_put(root);
        o->api_ok = 0;
        return;
    }

    OllamaModel old[MAX_MODELS];
    memcpy(old, o->models, sizeof(old));
    int old_count = o->model_count;

    memset(o->models, 0, sizeof(o->models));
    o->model_count = 0;

    int n = json_object_array_length(arr);
    if (n > MAX_MODELS) n = MAX_MODELS;

    for (int i = 0; i < n; i++) {
        json_object *mo = json_object_array_get_idx(arr, i);
        OllamaModel *m = &o->models[o->model_count++];

        json_copy_string(mo, "name", m->name, sizeof(m->name));
        m->size_bytes = json_u64(mo, "size");
        m->size_vram_bytes = json_u64(mo, "size_vram");
        m->context_loaded = json_u64(mo, "context_length");

        json_object *details = NULL;
        if (json_object_object_get_ex(mo, "details", &details)) {
            json_copy_string(details, "parameter_size", m->parameter_size, sizeof(m->parameter_size));
            json_copy_string(details, "quantization_level", m->quantization, sizeof(m->quantization));
            json_copy_string(details, "family", m->family, sizeof(m->family));
        }

        for (int j = 0; j < old_count; j++) {
            if (strcmp(old[j].name, m->name) == 0) {
                m->context_model_max = old[j].context_model_max;
                break;
            }
        }
        if (!m->context_model_max) ollama_fetch_show(o, m);
    }

    o->api_ok = 1;
    json_object_put(root);
}

static void llama_fetch_props(OllamaStats *o) {
    char url[512];
    snprintf(url, sizeof(url), "%s/props", o->endpoint);
    char *body = http_request(url, NULL);
    if (!body) {
        o->api_ok = 0;
        return;
    }

    json_object *root = json_tokener_parse(body);
    free(body);
    if (!root) {
        o->api_ok = 0;
        return;
    }

    OllamaModel *m = &o->models[0];
    memset(o->models, 0, sizeof(o->models));
    o->model_count = 1;

    char model_path[512] = {0};
    char model_alias[160] = {0};
    char model_ftype[32] = {0};
    json_copy_string(root, "model_path", model_path, sizeof(model_path));
    json_copy_string(root, "model_alias", model_alias, sizeof(model_alias));
    json_copy_string(root, "model_ftype", model_ftype, sizeof(model_ftype));
    const char *base = strrchr(model_path, '/');
    base = base ? base + 1 : model_path;

    if (strstr(base, "5440074c687a")) {
        snprintf(m->name, sizeof(m->name), "%s", "qwen38-dyn3-balanced-q4-256k");
        snprintf(m->parameter_size, sizeof(m->parameter_size), "%s", "27.3B");
        snprintf(m->quantization, sizeof(m->quantization), "%s", "Q4_K_M");
        m->context_model_max = 262144;
    } else if (strstr(base, "fd4730dd8aad")) {
        snprintf(m->name, sizeof(m->name), "%s", "qwen38-dyn3-speed-q2-256k");
        snprintf(m->parameter_size, sizeof(m->parameter_size), "%s", "27.3B");
        snprintf(m->quantization, sizeof(m->quantization), "%s", "Dynamic Q2");
        m->context_model_max = 262144;
    } else {
        snprintf(m->name, sizeof(m->name), "%.150s",
                 model_alias[0] ? model_alias : (base[0] ? base : "llama-server model"));
    }

    if (!m->quantization[0] && model_ftype[0])
        snprintf(m->quantization, sizeof(m->quantization), "%s", model_ftype);

    if (strcasestr(model_alias, "qwen3.8") || strcasestr(base, "qwen3.8")) {
        snprintf(m->family, sizeof(m->family), "%s", "qwen3.8");
        if (strcasestr(model_alias, "27b") || strcasestr(base, "27b"))
            snprintf(m->parameter_size, sizeof(m->parameter_size), "%s", "27.3B");
        if (!m->context_model_max) m->context_model_max = 262144;
    } else if (strcasestr(model_alias, "qwen3.6") || strcasestr(base, "qwen3.6")) {
        snprintf(m->family, sizeof(m->family), "%s", "qwen3.6");
        if (strcasestr(model_alias, "35b-a3b") || strcasestr(base, "35b-a3b"))
            snprintf(m->parameter_size, sizeof(m->parameter_size), "%s", "35B-A3B");
        else if (strcasestr(model_alias, "35b") || strcasestr(base, "35b"))
            snprintf(m->parameter_size, sizeof(m->parameter_size), "%s", "35B");
        if (!m->context_model_max) m->context_model_max = 262144;
    } else if (strcasestr(model_alias, "qwen") || strcasestr(base, "qwen")) {
        snprintf(m->family, sizeof(m->family), "%s", "qwen");
    } else if (!m->family[0]) {
        snprintf(m->family, sizeof(m->family), "%s", "llama.cpp");
    }

    struct stat st;
    if (model_path[0] && stat(model_path, &st) == 0)
        m->size_bytes = (uint64_t)st.st_size;

    json_object *slots = NULL;
    if (json_object_object_get_ex(root, "total_slots", &slots)) {
        int p = json_object_get_int(slots);
        if (p > 0) o->parallel = p;
    }

    json_object *settings = NULL, *nctx = NULL;
    if (json_object_object_get_ex(root, "default_generation_settings", &settings) &&
        json_object_is_type(settings, json_type_object) &&
        json_object_object_get_ex(settings, "n_ctx", &nctx)) {
        uint64_t total_ctx = (uint64_t)json_object_get_int64(nctx);
        m->context_loaded = total_ctx;
        if (!m->context_model_max) m->context_model_max = total_ctx;
        o->slot_context = o->parallel > 0 ? total_ctx / (uint64_t)o->parallel : total_ctx;
    }

    o->api_ok = 1;
    json_object_put(root);
}

static void set_event(OllamaStats *o, const char *msg);

static void llama_fetch_slots(OllamaStats *o) {
    char url[512];
    snprintf(url, sizeof(url), "%s/slots", o->endpoint);
    char *body = http_request(url, NULL);
    if (!body) return;

    json_object *root = json_tokener_parse(body);
    free(body);
    if (!root || !json_object_is_type(root, json_type_array)) {
        if (root) json_object_put(root);
        return;
    }

    json_object *selected = NULL;
    int count = json_object_array_length(root);
    for (int i = 0; i < count; i++) {
        json_object *slot = json_object_array_get_idx(root, i);
        json_object *processing = NULL;
        if (!selected) selected = slot;
        if (json_object_object_get_ex(slot, "is_processing", &processing) &&
            json_object_get_boolean(processing)) {
            selected = slot;
            break;
        }
    }

    if (!selected) {
        o->phase = PHASE_IDLE;
        json_object_put(root);
        return;
    }

    json_object *value = NULL;
    int processing = json_object_object_get_ex(selected, "is_processing", &value) &&
                     json_object_get_boolean(value);
    o->slot_id = (int)json_u64(selected, "id");
    o->task_id = (long long)json_u64(selected, "id_task");
    uint64_t n_ctx = json_u64(selected, "n_ctx");
    if (n_ctx) o->slot_context = n_ctx;

    if (processing) {
        uint64_t prompt_total = json_u64(selected, "n_prompt_tokens");
        uint64_t prompt_processed = json_u64(selected, "n_prompt_tokens_processed");
        uint64_t prompt_cached = json_u64(selected, "n_prompt_tokens_cache");
        uint64_t decoded = 0;

        json_object *next = NULL;
        if (json_object_object_get_ex(selected, "next_token", &next) &&
            json_object_is_type(next, json_type_array) &&
            json_object_array_length(next) > 0) {
            decoded = json_u64(json_object_array_get_idx(next, 0), "n_decoded");
        }

        o->prompt_tokens = prompt_total;
        o->prompt_processed_tokens = prompt_processed;
        o->cached_prompt_tokens = prompt_cached;
        o->prompt_total_est = prompt_total;
        o->decoded_tokens = decoded;
        if (prompt_total) {
            uint64_t done = prompt_processed + prompt_cached;
            if (done > prompt_total) done = prompt_total;
            o->prompt_progress = (double)done / (double)prompt_total;
        }
        o->phase = decoded > 0 ? PHASE_GENERATING : PHASE_PREFILL;
        set_event(o, decoded > 0 ? "slot generating" : "slot prefill");
    } else {
        o->phase = PHASE_IDLE;
        set_event(o, "slot idle");
    }

    json_object_put(root);
}

static void llama_fetch_profile(OllamaStats *o) {
    if (o->unsloth) {
        snprintf(o->profile, sizeof(o->profile), "%s", "unsloth-studio");
        return;
    }
    char *body = http_request("http://127.0.0.1:11434/_profile/status", NULL);
    if (!body) return;
    json_object *root = json_tokener_parse(body);
    free(body);
    if (!root) return;
    json_copy_string(root, "profile", o->profile, sizeof(o->profile));
    json_object_put(root);
}

static long llama_find_pid(void) {
    FILE *pp = popen("pgrep -n -x llama-server", "r");
    if (!pp) return -1;
    long pid = -1;
    (void)fscanf(pp, "%ld", &pid);
    pclose(pp);
    return pid;
}

static int llama_discover_endpoint(char *endpoint, size_t endpoint_size, long *pid_out) {
    long pid = llama_find_pid();
    if (pid <= 0) return 0;

    char path[64];
    snprintf(path, sizeof(path), "/proc/%ld/cmdline", pid);
    FILE *fp = fopen(path, "rb");
    if (!fp) return 0;
    char buf[16384];
    size_t n = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    if (!n) return 0;
    buf[n] = '\0';

    long port = -1;
    const char *arg[512];
    int argc = 0;
    for (size_t pos = 0; pos < n && argc < (int)(sizeof(arg) / sizeof(arg[0]));) {
        arg[argc++] = &buf[pos];
        pos += strlen(&buf[pos]) + 1;
    }
    for (int i = 1; i < argc; i++) {
        const char *value = NULL;
        if ((!strcmp(arg[i], "--port") || !strcmp(arg[i], "-p")) && i + 1 < argc)
            value = arg[++i];
        else if (!strncmp(arg[i], "--port=", 7))
            value = arg[i] + 7;

        if (value) {
            errno = 0;
            char *end = NULL;
            long candidate = strtol(value, &end, 10);
            if (!errno && end != value && *end == '\0' && candidate > 0 && candidate <= 65535)
                port = candidate;
        }
    }
    if (port <= 0) return 0;

    snprintf(endpoint, endpoint_size, "http://127.0.0.1:%ld", port);
    if (pid_out) *pid_out = pid;
    return 1;
}

static const char *backend_mode_name(BackendMode mode) {
    switch (mode) {
        case BACKEND_LLAMA:  return "LLAMA";
        case BACKEND_OLLAMA: return "OLLAMA";
        default:             return "AUTO";
    }
}

/* Select the telemetry source.  AUTO is intentionally re-evaluated while the
 * monitor is running: the profile router starts llama-server lazily, so a
 * one-shot decision at gpumon startup otherwise remains stuck on Ollama. */
static int select_backend(OllamaStats *o, BackendMode mode,
                          const char *ollama_endpoint,
                          char *llama_endpoint, size_t llama_endpoint_size) {
    char old_backend[sizeof(o->backend)];
    char old_endpoint[sizeof(o->endpoint)];
    long old_pid = o->llama_pid;
    snprintf(old_backend, sizeof(old_backend), "%s", o->backend);
    snprintf(old_endpoint, sizeof(old_endpoint), "%s", o->endpoint);

    char detected[256] = {0};
    long pid = -1;
    int llama_available = 0;
    if (mode != BACKEND_OLLAMA) {
        llama_available = llama_discover_endpoint(detected, sizeof(detected), &pid);
        if (llama_available)
            snprintf(llama_endpoint, llama_endpoint_size, "%s", detected);
    }

    if (mode == BACKEND_LLAMA || (mode == BACKEND_AUTO && llama_available)) {
        snprintf(o->backend, sizeof(o->backend), "%s", "LLAMA");
        snprintf(o->endpoint, sizeof(o->endpoint), "%s", llama_endpoint);
        o->llama_pid = llama_available ? pid : llama_find_pid();
    } else {
        snprintf(o->backend, sizeof(o->backend), "%s", "OLLAMA");
        snprintf(o->endpoint, sizeof(o->endpoint), "%s", ollama_endpoint);
        o->llama_pid = -1;
    }

    int changed = strcmp(old_backend, o->backend) != 0 ||
                  strcmp(old_endpoint, o->endpoint) != 0 ||
                  old_pid != o->llama_pid;
    if (changed) {
        o->api_ok = 0;
        o->phase = PHASE_UNKNOWN;
        o->model_count = 0;
        o->profile[0] = '\0';
        o->last_event[0] = '\0';
        o->unsloth = 0;
        o->metric_prompt_tokens = 0;
        o->metric_cached_tokens = 0;
        o->metric_predicted_tokens = 0;
        o->metric_draft_tokens = 0;
        o->metric_accepted_tokens = 0;
        o->mtp_acceptance_pct = 0.0;
        o->requests_processing = 0;
        o->requests_deferred = 0;
        o->kv_k[0] = o->kv_v[0] = '\0';
        o->kv_k_draft[0] = o->kv_v_draft[0] = '\0';
        o->split_mode[0] = o->layers_split[0] = '\0';
        o->gpu_layers[0] = o->gpu_layers_draft[0] = '\0';
        o->spec_type[0] = '\0';
        o->batch_size = o->ubatch_size = 0;
        o->spec_draft_n_max = 0;
        o->kv_total_mib = o->kv_k_mib = o->kv_v_mib = 0.0;
    }
    return changed;
}

static void llama_read_cmdline(OllamaStats *o) {
    long pid = o->llama_pid > 0 ? o->llama_pid : llama_find_pid();
    if (pid <= 0) return;
    o->llama_pid = pid;

    char path[64];
    snprintf(path, sizeof(path), "/proc/%ld/cmdline", pid);
    FILE *fp = fopen(path, "rb");
    if (!fp) return;
    char buf[16384];
    size_t n = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    if (!n) return;
    buf[n] = '\0';

    const char *arg[512];
    int argc = 0;
    for (size_t pos = 0; pos < n && argc < (int)(sizeof(arg) / sizeof(arg[0]));) {
        arg[argc++] = &buf[pos];
        pos += strlen(&buf[pos]) + 1;
    }
    o->unsloth = argc > 0 && strstr(arg[0], "/unsloth-studio/") != NULL;
    for (int i = 1; i < argc; i++) {
        if ((!strcmp(arg[i], "-ctk") || !strcmp(arg[i], "--cache-type-k")) && i + 1 < argc)
            snprintf(o->kv_k, sizeof(o->kv_k), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ctv") || !strcmp(arg[i], "--cache-type-v")) && i + 1 < argc)
            snprintf(o->kv_v, sizeof(o->kv_v), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ctkd") || !strcmp(arg[i], "--cache-type-k-draft")) && i + 1 < argc)
            snprintf(o->kv_k_draft, sizeof(o->kv_k_draft), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ctvd") || !strcmp(arg[i], "--cache-type-v-draft")) && i + 1 < argc)
            snprintf(o->kv_v_draft, sizeof(o->kv_v_draft), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-fa") || !strcmp(arg[i], "--flash-attn")) && i + 1 < argc) {
            const char *v = arg[++i];
            o->flash_attn = (!strcmp(v, "on") || !strcmp(v, "true") || !strcmp(v, "1"));
        } else if ((!strcmp(arg[i], "-sm") || !strcmp(arg[i], "--split-mode")) && i + 1 < argc)
            snprintf(o->split_mode, sizeof(o->split_mode), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ts") || !strcmp(arg[i], "--tensor-split")) && i + 1 < argc)
            snprintf(o->layers_split, sizeof(o->layers_split), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ngl") || !strcmp(arg[i], "--gpu-layers") ||
                  !strcmp(arg[i], "--n-gpu-layers")) && i + 1 < argc)
            snprintf(o->gpu_layers, sizeof(o->gpu_layers), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-ngld") || !strcmp(arg[i], "--gpu-layers-draft")) && i + 1 < argc)
            snprintf(o->gpu_layers_draft, sizeof(o->gpu_layers_draft), "%s", arg[++i]);
        else if ((!strcmp(arg[i], "-np") || !strcmp(arg[i], "--parallel")) && i + 1 < argc)
            o->parallel = atoi(arg[++i]);
        else if ((!strcmp(arg[i], "-b") || !strcmp(arg[i], "--batch-size")) && i + 1 < argc)
            o->batch_size = atoi(arg[++i]);
        else if ((!strcmp(arg[i], "-ub") || !strcmp(arg[i], "--ubatch-size")) && i + 1 < argc)
            o->ubatch_size = atoi(arg[++i]);
        else if (!strcmp(arg[i], "--spec-type") && i + 1 < argc)
            snprintf(o->spec_type, sizeof(o->spec_type), "%s", arg[++i]);
        else if (!strcmp(arg[i], "--spec-draft-n-max") && i + 1 < argc)
            o->spec_draft_n_max = atoi(arg[++i]);
        else if ((!strcmp(arg[i], "-m") || !strcmp(arg[i], "--model")) && i + 1 < argc) {
            if (strstr(arg[i + 1], "models--unsloth--") ||
                strstr(arg[i + 1], "/unsloth-studio/"))
                o->unsloth = 1;
            i++;
        }
    }

    if (o->unsloth)
        snprintf(o->profile, sizeof(o->profile), "%s", "unsloth-studio");

    if (!o->split_mode[0])
        snprintf(o->split_mode, sizeof(o->split_mode), "%s", "layer(default)");
    if (strstr(o->spec_type, "draft-mtp")) {
        if (!o->kv_k_draft[0] && o->kv_k[0])
            snprintf(o->kv_k_draft, sizeof(o->kv_k_draft), "%s", o->kv_k);
        if (!o->kv_v_draft[0] && o->kv_v[0])
            snprintf(o->kv_v_draft, sizeof(o->kv_v_draft), "%s", o->kv_v);
    }

    if (o->parallel < 1) o->parallel = 1;
    /* Defaults reported by the bundled Unsloth llama-server build.  Tuned
     * MMQ services pass both values explicitly and therefore override these. */
    if (o->batch_size < 1) o->batch_size = 2048;
    if (o->ubatch_size < 1) o->ubatch_size = 512;
    if (o->model_count > 0 && o->models[0].context_loaded)
        o->slot_context = o->models[0].context_loaded / (uint64_t)o->parallel;
}

static long long parse_ll_after(const char *s, const char *needle, long long fallback) {
    const char *p = strstr(s, needle);
    if (!p) return fallback;
    p += strlen(needle);
    while (*p == ' ' || *p == '=') p++;
    errno = 0;
    char *end = NULL;
    long long v = strtoll(p, &end, 10);
    if (errno || end == p) return fallback;
    return v;
}

static double parse_double_after(const char *s, const char *needle, double fallback) {
    const char *p = strstr(s, needle);
    if (!p) return fallback;
    p += strlen(needle);
    while (*p == ' ' || *p == '=') p++;
    errno = 0;
    char *end = NULL;
    static locale_t c_numeric = (locale_t)0;
    if (!c_numeric) c_numeric = newlocale(LC_NUMERIC_MASK, "C", (locale_t)0);
    double v = c_numeric ? strtod_l(p, &end, c_numeric) : strtod(p, &end);
    if (errno || end == p) return fallback;
    return v;
}

static int extract_quoted_value(const char *s, const char *needle, char *dst, size_t dstsz) {
    const char *p = strstr(s, needle);
    if (!p) return 0;
    p += strlen(needle);
    const char *e = strchr(p, '\'');
    if (!e) return 0;
    size_t n = (size_t)(e - p);
    if (n >= dstsz) n = dstsz - 1;
    memcpy(dst, p, n);
    dst[n] = '\0';
    return 1;
}

static void set_event(OllamaStats *o, const char *msg) {
    snprintf(o->last_event, sizeof(o->last_event), "%.155s", msg);
    clock_gettime(CLOCK_MONOTONIC, &o->last_activity);
}

static void parse_ollama_log(OllamaStats *o, const char *msg) {
    if (!msg || !*msg) return;

    if (strstr(msg, "all slots are idle") || strstr(msg, "stop processing:")) {
        o->phase = PHASE_IDLE;
        set_event(o, "runner idle");
    }

    const char *np = strstr(msg, "new prompt");
    if (np) {
        o->phase = PHASE_PREFILL;
        o->slot_context = (uint64_t)parse_ll_after(msg, "n_ctx_slot", (long long)o->slot_context);
        o->prompt_tokens = (uint64_t)parse_ll_after(msg, "task.n_tokens", (long long)o->prompt_tokens);
        o->prompt_total_est = o->prompt_tokens;
        o->prompt_processed_tokens = 0;
        o->prompt_progress = 0.0;
        o->decoded_tokens = 0;
        o->gen_tps = 0.0;
        o->gen_tps_3s = 0.0;
        o->slot_id = (int)parse_ll_after(msg, "id", o->slot_id);
        o->task_id = parse_ll_after(msg, "task", o->task_id);
        set_event(o, "new prompt");
    }

    if (strstr(msg, "prompt processing")) {
        o->phase = PHASE_PREFILL;
        o->prompt_processed_tokens = (uint64_t)parse_ll_after(
            msg, "n_tokens", (long long)o->prompt_processed_tokens);
        o->prompt_progress = parse_double_after(msg, "progress", o->prompt_progress);
        const char *slash = strstr(msg, "s /");
        if (slash) {
            o->prompt_tps = parse_double_after(slash, "/", o->prompt_tps);
            if (o->prompt_tps > 0) o->last_prompt_tps = o->prompt_tps;
        }
        if (o->prompt_progress > 0.001 && o->prompt_progress <= 1.0) {
            uint64_t estimated = (uint64_t)llround(
                (double)o->prompt_processed_tokens / o->prompt_progress);
            if (!o->prompt_tokens || estimated > o->prompt_tokens)
                o->prompt_total_est = estimated;
        }
        set_event(o, "prompt processing");
    }

    if ((strstr(msg, "n_decoded") || strstr(msg, "n_gen")) &&
        strstr(msg, "tg =")) {

        o->phase = PHASE_GENERATING;

        if (strstr(msg, "n_gen")) {
            o->decoded_tokens = (uint64_t)parse_ll_after(
                msg, "n_gen", (long long)o->decoded_tokens);
        } else {
            o->decoded_tokens = (uint64_t)parse_ll_after(
                msg, "n_decoded", (long long)o->decoded_tokens);
        }

        o->gen_tps = parse_double_after(msg, "tg", o->gen_tps);

        if (strstr(msg, "tg_3s")) {
            o->gen_tps_3s = parse_double_after(
                msg, "tg_3s", o->gen_tps_3s);
        }

        if (o->gen_tps > 0)
            o->last_gen_tps = o->gen_tps;

        set_event(o, "generating");
    }

    const char *acceptance = strstr(msg, "draft acceptance =");
    if (acceptance) {
        double ratio = 0.0;
        unsigned long long accepted = 0, generated = 0;
        int rc = sscanf(acceptance,
                        "draft acceptance = %lf ( %llu accepted / %llu generated)",
                        &ratio, &accepted, &generated);
        if (rc >= 1) o->mtp_acceptance_pct = ratio * 100.0;
        if (rc >= 3) {
            o->metric_accepted_tokens = (uint64_t)accepted;
            o->metric_draft_tokens = (uint64_t)generated;
        }
    }

    if (strstr(msg, "prompt eval time")) {
        const char *comma = strrchr(msg, ',');
        if (comma && strstr(comma, "tokens per second")) {
            double v = strtod(comma + 1, NULL);
            if (v > 0) {
                o->prompt_tps = v;
                o->last_prompt_tps = v;
            }
        }
        const char *slash = strstr(msg, "ms /");
        if (slash) {
            long long tokens = parse_ll_after(slash, "/", 0);
            if (tokens > 0) o->last_prompt_tokens = (uint64_t)tokens;
        }
    } else if (strstr(msg, "eval time") && strstr(msg, "tokens per second")) {
        const char *comma = strrchr(msg, ',');
        if (comma) {
            double v = strtod(comma + 1, NULL);
            if (v > 0) {
                o->gen_tps = v;
                o->last_gen_tps = v;
            }
        }
    }

    if (strstr(msg, "--flash-attn"))
        o->flash_attn = 1;
    if (strstr(msg, "flash attention disabled") ||
        strstr(msg, "flash attention enabled but not supported"))
        o->flash_attn = 0;

    if (strstr(msg, "parallel=")) {
        int p = (int)parse_ll_after(msg, "parallel", o->parallel);
        if (p > 0) o->parallel = p;
    }

    if (strstr(msg, "layers.model=") || strstr(msg, "layers.offload=")) {
        int lm = (int)parse_ll_after(msg, "layers.model", o->layers_model);
        int lo = (int)parse_ll_after(msg, "layers.offload", o->layers_offload);
        if (lm >= 0) o->layers_model = lm;
        if (lo >= 0) o->layers_offload = lo;

        const char *sp = strstr(msg, "layers.split=");
        if (sp) {
            sp += strlen("layers.split=");
            if (*sp == '"') sp++;
            size_t i = 0;
            while (*sp && *sp != '"' && *sp != ' ' && i + 1 < sizeof(o->layers_split))
                o->layers_split[i++] = *sp++;
            o->layers_split[i] = '\0';
        }
    }

    /* Newer/older llama.cpp/Ollama KV cache log styles. */
    const char *kv = strstr(msg, "KV self size");
    if (kv) {
        double total = 0, km = 0, vm = 0;
        char kt[24] = {0}, vt[24] = {0};
        int rc = sscanf(kv,
                        "KV self size = %lf MiB, K (%23[^)]): %lf MiB, V (%23[^)]): %lf MiB",
                        &total, kt, &km, vt, &vm);
        if (rc >= 4) {
            o->kv_total_mib = total;
            snprintf(o->kv_k, sizeof(o->kv_k), "%s", kt);
            o->kv_k_mib = km;
            if (rc >= 5) {
                snprintf(o->kv_v, sizeof(o->kv_v), "%s", vt);
                o->kv_v_mib = vm;
            }
        }
    }

    if (strstr(msg, "type_k = '")) {
        extract_quoted_value(msg, "type_k = '", o->kv_k, sizeof(o->kv_k));
        extract_quoted_value(msg, "type_v = '", o->kv_v, sizeof(o->kv_v));
    }

    if (strstr(msg, "--kv-cache-type ")) {
        const char *p = strstr(msg, "--kv-cache-type ");
        p += strlen("--kv-cache-type ");
        char t[24] = {0};
        size_t i = 0;
        while (*p && *p != ' ' && *p != '"' && i + 1 < sizeof(t)) t[i++] = *p++;
        t[i] = '\0';
        if (t[0]) {
            snprintf(o->kv_k, sizeof(o->kv_k), "%s", t);
            snprintf(o->kv_v, sizeof(o->kv_v), "%s", t);
        }
    }
}

static int prometheus_number(const char *body, const char *key, double *value) {
    size_t key_len = strlen(key);
    const char *p = body;
    while ((p = strstr(p, key)) != NULL) {
        if ((p == body || p[-1] == '\n') &&
            (p[key_len] == ' ' || p[key_len] == '\t')) {
            p += key_len;
            while (*p == ' ' || *p == '\t') p++;
            errno = 0;
            char *end = NULL;
            double parsed = strtod(p, &end);
            if (!errno && end != p) {
                *value = parsed;
                return 1;
            }
        }
        p += key_len;
    }
    return 0;
}

static void llama_fetch_metrics(OllamaStats *o) {
    char url[512];
    snprintf(url, sizeof(url), "%s/metrics", o->endpoint);
    char *body = http_request(url, NULL);
    if (!body) return;

    double value = 0.0;
    if (prometheus_number(body, "llamacpp:prompt_tokens_seconds", &value)) {
        if (value > 0.0) {
            o->prompt_tps = value;
            o->last_prompt_tps = value;
        }
    }
    if (prometheus_number(body, "llamacpp:predicted_tokens_seconds", &value)) {
        if (value > 0.0) {
            o->gen_tps = value;
            o->last_gen_tps = value;
        }
    }
    if (prometheus_number(body, "llamacpp:prompt_tokens_total", &value))
        o->metric_prompt_tokens = (uint64_t)llround(value);
    if (prometheus_number(body, "llamacpp:prompt_tokens_cached_total", &value))
        o->metric_cached_tokens = (uint64_t)llround(value);
    if (prometheus_number(body, "llamacpp:tokens_predicted_total", &value))
        o->metric_predicted_tokens = (uint64_t)llround(value);
    if (prometheus_number(body, "llamacpp:spec_decode_num_draft_tokens_total", &value))
        o->metric_draft_tokens = (uint64_t)llround(value);
    if (prometheus_number(body, "llamacpp:spec_decode_num_accepted_tokens_total", &value))
        o->metric_accepted_tokens = (uint64_t)llround(value);
    if (prometheus_number(body, "llamacpp:requests_processing", &value))
        o->requests_processing = (int)llround(value);
    if (prometheus_number(body, "llamacpp:requests_deferred", &value))
        o->requests_deferred = (int)llround(value);

    if (o->metric_draft_tokens > 0) {
        o->mtp_acceptance_pct = 100.0 *
            (double)o->metric_accepted_tokens / (double)o->metric_draft_tokens;
    }
    free(body);
}

/* Unsloth redirects the child llama-server stdout/stderr to a per-port file
 * instead of journald.  Resolve the file through /proc so this also survives
 * Unsloth's random port and filename changes. */
static void llama_fetch_unsloth_log(OllamaStats *o) {
    if (!o->unsloth || o->llama_pid <= 0) return;

    char target[1024] = {0};
    for (int fd = 1; fd <= 2 && !target[0]; fd++) {
        char fd_path[64];
        snprintf(fd_path, sizeof(fd_path), "/proc/%ld/fd/%d", o->llama_pid, fd);
        ssize_t n = readlink(fd_path, target, sizeof(target) - 1);
        if (n <= 0) {
            target[0] = '\0';
            continue;
        }
        target[n] = '\0';
        if (!strstr(target, "/unsloth-studio/logs/llama-server/"))
            target[0] = '\0';
    }
    if (!target[0]) {
        const char *colon = strrchr(o->endpoint, ':');
        long port = colon ? strtol(colon + 1, NULL, 10) : 0;
        if (port > 0 && port <= 65535) {
            char pattern[256];
            snprintf(pattern, sizeof(pattern),
                     "/opt/unsloth-studio/logs/llama-server/llama-*-port-%ld-*.log",
                     port);
            glob_t matches = {0};
            if (glob(pattern, 0, NULL, &matches) == 0) {
                time_t newest_time = 0;
                for (size_t i = 0; i < matches.gl_pathc; i++) {
                    struct stat st;
                    if (stat(matches.gl_pathv[i], &st) == 0 &&
                        (!target[0] || st.st_mtime >= newest_time)) {
                        newest_time = st.st_mtime;
                        snprintf(target, sizeof(target), "%s", matches.gl_pathv[i]);
                    }
                }
            }
            globfree(&matches);
        }
    }
    if (!target[0]) return;

    FILE *fp = fopen(target, "r");
    if (!fp) return;
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return;
    }
    long end = ftell(fp);
    long start = end > 524288L ? end - 524288L : 0L;
    if (fseek(fp, start, SEEK_SET) != 0) {
        fclose(fp);
        return;
    }

    char line[8192];
    if (start > 0) (void)fgets(line, sizeof(line), fp); /* discard partial line */
    while (fgets(line, sizeof(line), fp))
        parse_ollama_log(o, line);
    fclose(fp);
}

static int init_journal(sd_journal **out, OllamaStats *o) {
    sd_journal *j = NULL;
    int rc = sd_journal_open(&j, SD_JOURNAL_LOCAL_ONLY);
    if (rc < 0) return rc;

    const char *llama_units[] = {
        "_SYSTEMD_UNIT=llama-qwen38-q4.service",
        "_SYSTEMD_UNIT=llama-qwen38-q4-accurate.service",
        "_SYSTEMD_UNIT=llama-qwen38-q4-fast.service",
        "_SYSTEMD_UNIT=llama-qwen38-q2-speed.service",
    };
    if (strcmp(o->backend, "LLAMA") == 0) {
        if (o->llama_pid > 0) {
            char match[64];
            snprintf(match, sizeof(match), "_PID=%ld", o->llama_pid);
            rc = sd_journal_add_match(j, match, 0);
        } else {
            rc = sd_journal_add_match(j, llama_units[0], 0);
            for (size_t i = 1; rc >= 0 && i < sizeof(llama_units) / sizeof(llama_units[0]); i++) {
                rc = sd_journal_add_disjunction(j);
                if (rc >= 0) rc = sd_journal_add_match(j, llama_units[i], 0);
            }
        }
    } else {
        rc = sd_journal_add_match(j, "_SYSTEMD_UNIT=ollama.service", 0);
    }
    if (rc < 0) {
        sd_journal_close(j);
        return rc;
    }

    sd_journal_seek_tail(j);
    sd_journal_previous_skip(j, 3000);

    while (sd_journal_next(j) > 0) {
        const void *data = NULL;
        size_t len = 0;
        if (sd_journal_get_data(j, "MESSAGE", &data, &len) == 0 && len > 8) {
            const char *raw = (const char *)data;
            const char *msg = raw + 8; /* "MESSAGE=" */
            size_t mlen = len - 8;
            char *copy = strndup(msg, mlen);
            if (copy) {
                parse_ollama_log(o, copy);
                free(copy);
            }
        }
    }

    *out = j;
    return 0;
}

static void poll_journal(sd_journal *j, OllamaStats *o) {
    if (!j) return;

    sd_journal_process(j);
    while (sd_journal_next(j) > 0) {
        const void *data = NULL;
        size_t len = 0;
        if (sd_journal_get_data(j, "MESSAGE", &data, &len) == 0 && len > 8) {
            const char *raw = (const char *)data;
            const char *msg = raw + 8;
            size_t mlen = len - 8;
            char *copy = strndup(msg, mlen);
            if (copy) {
                parse_ollama_log(o, copy);
                free(copy);
            }
        }
    }
}

static void gpu_update(GpuStats *g, int hist_target) {
    nvmlUtilization_t u = {0};
    nvmlMemory_t mem = {0};
    nvmlFieldValue_t memory_temp = {0};

    if (nvmlDeviceGetUtilizationRates(g->h, &u) == NVML_SUCCESS) {
        g->gpu_util = u.gpu;
        g->mem_util = u.memory;
    }

    if (nvmlDeviceGetMemoryInfo(g->h, &mem) == NVML_SUCCESS) {
        g->mem_total = mem.total;
        g->mem_used = mem.used;
    }

    nvmlDeviceGetTemperature(g->h, NVML_TEMPERATURE_GPU, &g->temp);
    memory_temp.fieldId = NVML_FI_DEV_MEMORY_TEMP;
    if (nvmlDeviceGetFieldValues(g->h, 1, &memory_temp) == NVML_SUCCESS &&
        memory_temp.nvmlReturn == NVML_SUCCESS &&
        memory_temp.valueType == NVML_VALUE_TYPE_UNSIGNED_INT)
        g->memory_temp = memory_temp.value.uiVal;
    nvmlDeviceGetTemperatureThreshold(g->h, NVML_TEMPERATURE_THRESHOLD_MEM_MAX,
                                      &g->memory_max_temp);
    nvmlDeviceGetPowerUsage(g->h, &g->power_mw);
    nvmlDeviceGetPowerManagementLimit(g->h, &g->power_limit_mw);
    nvmlDeviceGetClockInfo(g->h, NVML_CLOCK_SM, &g->sm_clock);
    nvmlDeviceGetClockInfo(g->h, NVML_CLOCK_MEM, &g->mem_clock);
    nvmlDeviceGetPerformanceState(g->h, &g->pstate);
    nvmlDeviceGetCurrentClocksThrottleReasons(g->h, &g->throttle_reasons);

    nvmlDeviceGetCurrPcieLinkGeneration(g->h, &g->pcie_gen);
    nvmlDeviceGetCurrPcieLinkWidth(g->h, &g->pcie_width);
    nvmlDeviceGetMaxPcieLinkGeneration(g->h, &g->pcie_gen_max);
    nvmlDeviceGetMaxPcieLinkWidth(g->h, &g->pcie_width_max);

    g->pcie_capacity_MBps = pcie_lane_MBps(g->pcie_gen) * g->pcie_width;

    unsigned int rx_kbs = 0, tx_kbs = 0;
    if (nvmlDeviceGetPcieThroughput(g->h, NVML_PCIE_UTIL_RX_BYTES, &rx_kbs) == NVML_SUCCESS)
        g->rx_MBps = (double)rx_kbs / 1024.0;
    if (nvmlDeviceGetPcieThroughput(g->h, NVML_PCIE_UTIL_TX_BYTES, &tx_kbs) == NVML_SUCCESS)
        g->tx_MBps = (double)tx_kbs / 1024.0;

    if (g->rx_MBps > g->rx_peak) g->rx_peak = g->rx_MBps;
    if (g->tx_MBps > g->tx_peak) g->tx_peak = g->tx_MBps;

    if (hist_target < 1) hist_target = 1;
    if (hist_target > HIST_MAX) hist_target = HIST_MAX;

    g->rx_hist[g->hist_pos] = g->rx_MBps;
    g->tx_hist[g->hist_pos] = g->tx_MBps;
    g->hist_pos = (g->hist_pos + 1) % hist_target;
    if (g->hist_count < hist_target) g->hist_count++;
}

static const char *phase_name(OllamaPhase p) {
    switch (p) {
        case PHASE_IDLE: return "IDLE";
        case PHASE_PREFILL: return "PREFILL";
        case PHASE_GENERATING: return "GENERATING";
        default: return "UNKNOWN";
    }
}

static void print_json_snapshot(const GpuStats *gpu, unsigned int gpu_count,
                                const OllamaStats *o) {
    json_object *root = json_object_new_object();
    json_object *gpus = json_object_new_array();
    for (unsigned int i = 0; i < gpu_count; i++) {
        const GpuStats *g = &gpu[i];
        json_object *item = json_object_new_object();
        json_object_object_add(item, "index", json_object_new_int((int)i));
        json_object_object_add(item, "name", json_object_new_string(g->name));
        json_object_object_add(item, "gpuUtil", json_object_new_int((int)g->gpu_util));
        json_object_object_add(item, "memoryUtil", json_object_new_int((int)g->mem_util));
        json_object_object_add(item, "vramUsedBytes", json_object_new_int64((int64_t)g->mem_used));
        json_object_object_add(item, "vramTotalBytes", json_object_new_int64((int64_t)g->mem_total));
        json_object_object_add(item, "temperatureC", json_object_new_int((int)g->temp));
        json_object_object_add(item, "hbmTemperatureC", json_object_new_int((int)g->memory_temp));
        json_object_object_add(item, "hbmMaxTemperatureC", json_object_new_int((int)g->memory_max_temp));
        json_object_object_add(item, "powerW", json_object_new_double(g->power_mw / 1000.0));
        json_object_object_add(item, "powerLimitW", json_object_new_double(g->power_limit_mw / 1000.0));
        json_object_object_add(item, "clockMHz", json_object_new_int((int)g->sm_clock));
        json_object_object_add(item, "memoryClockMHz", json_object_new_int((int)g->mem_clock));
        json_object_object_add(item, "clockThrottleMask", json_object_new_int64((int64_t)g->throttle_reasons));
        json_object_object_add(item, "pcieGeneration", json_object_new_int((int)g->pcie_gen));
        json_object_object_add(item, "pcieWidth", json_object_new_int((int)g->pcie_width));
        json_object_object_add(item, "pcieRxMBps", json_object_new_double(g->rx_MBps));
        json_object_object_add(item, "pcieTxMBps", json_object_new_double(g->tx_MBps));
        json_object_array_add(gpus, item);
    }
    json_object_object_add(root, "gpus", gpus);

    json_object *runtime = json_object_new_object();
    const OllamaModel *m = o->model_count > 0 ? &o->models[0] : NULL;
    json_object_object_add(runtime, "backend", json_object_new_string(o->backend));
    json_object_object_add(runtime, "endpoint", json_object_new_string(o->endpoint));
    json_object_object_add(runtime, "apiOk", json_object_new_boolean(o->api_ok));
    json_object_object_add(runtime, "phase", json_object_new_string(phase_name(o->phase)));
    json_object_object_add(runtime, "model", json_object_new_string(m ? m->name : ""));
    json_object_object_add(runtime, "quantization", json_object_new_string(m ? m->quantization : ""));
    json_object_object_add(runtime, "contextLoaded", json_object_new_int64(m ? (int64_t)m->context_loaded : 0));
    json_object_object_add(runtime, "slotContext", json_object_new_int64((int64_t)o->slot_context));
    json_object_object_add(runtime, "promptTotal", json_object_new_int64((int64_t)o->prompt_total_est));
    json_object_object_add(runtime, "promptProcessed", json_object_new_int64((int64_t)o->prompt_processed_tokens));
    json_object_object_add(runtime, "promptProgress", json_object_new_double(o->prompt_progress));
    json_object_object_add(runtime, "promptTps", json_object_new_double(o->prompt_tps));
    json_object_object_add(runtime, "lastPromptTps", json_object_new_double(o->last_prompt_tps));
    json_object_object_add(runtime, "lastPromptTokens", json_object_new_int64((int64_t)o->last_prompt_tokens));
    json_object_object_add(runtime, "decodedTokens", json_object_new_int64((int64_t)o->decoded_tokens));
    json_object_object_add(runtime, "generationTps", json_object_new_double(o->gen_tps));
    json_object_object_add(runtime, "generationTps3s", json_object_new_double(o->gen_tps_3s));
    json_object_object_add(runtime, "lastGenerationTps", json_object_new_double(o->last_gen_tps));
    json_object_object_add(runtime, "batch", json_object_new_int(o->batch_size));
    json_object_object_add(runtime, "ubatch", json_object_new_int(o->ubatch_size));
    json_object_object_add(runtime, "mtpMax", json_object_new_int(o->spec_draft_n_max));
    json_object_object_add(runtime, "mtpAcceptancePct", json_object_new_double(o->mtp_acceptance_pct));
    json_object_object_add(runtime, "kvK", json_object_new_string(o->kv_k));
    json_object_object_add(runtime, "kvV", json_object_new_string(o->kv_v));
    json_object_object_add(runtime, "event", json_object_new_string(o->last_event));
    json_object_object_add(root, "runtime", runtime);
    puts(json_object_to_json_string_ext(root, JSON_C_TO_STRING_PLAIN));
    json_object_put(root);
}

static int pct_color(double pct) {
    if (!has_colors()) return 0;
    if (pct >= 85.0) return 3;
    if (pct >= 60.0) return 2;
    return 1;
}

static void bar_line(int row, int col, int width, const char *label,
                     double pct, const char *suffix) {
    if (width < 10) width = 10;
    pct = clamp_pct(pct);

    int labelw = 8;
    int suffixw = suffix ? (int)strlen(suffix) + 1 : 0;
    int barw = width - labelw - suffixw - 8;
    if (barw < 5) barw = 5;

    mvprintw(row, col, "%-7s [", label);

    int fill = (int)llround((pct > 100.0 ? 100.0 : pct) / 100.0 * barw);
    int cp = pct_color(pct);
    if (cp) attron(COLOR_PAIR(cp));
    for (int i = 0; i < barw; i++) addch(i < fill ? '#' : '.');
    if (cp) attroff(COLOR_PAIR(cp));

    printw("] %6.1f%%", pct);
    if (suffix) printw(" %s", suffix);
}

static void draw_gpu(int row, int col, int width, int idx, const GpuStats *g) {
    char suf[128];

    mvprintw(row++, col, "GPU %d  %s", idx, g->name);

    snprintf(suf, sizeof(suf), "%u%%", g->gpu_util);
    bar_line(row++, col, width, "GPU BUSY", g->gpu_util, suf);

    double vram_pct = g->mem_total ? 100.0 * (double)g->mem_used / (double)g->mem_total : 0.0;
    snprintf(suf, sizeof(suf), "%.2f/%.2f GiB", bytes_to_gib(g->mem_used), bytes_to_gib(g->mem_total));
    bar_line(row++, col, width, "VRAM", vram_pct, suf);

    snprintf(suf, sizeof(suf), "%u%% controller", g->mem_util);
    bar_line(row++, col, width, "MEM CTRL", g->mem_util, suf);

    double ppct = g->power_limit_mw ? 100.0 * g->power_mw / g->power_limit_mw : 0.0;
    snprintf(suf, sizeof(suf), "%.1f/%.1f W", g->power_mw / 1000.0, g->power_limit_mw / 1000.0);
    bar_line(row++, col, width, "POWER", ppct, suf);

    mvprintw(row++, col, "TEMP    GPU %u C  HBM %u/%u C  CLOCK %u MHz  MEM %u MHz  P%d",
             g->temp, g->memory_temp, g->memory_max_temp,
             g->sm_clock, g->mem_clock, (int)g->pstate);
    mvprintw(row++, col, "THROTTLE mask 0x%llx", g->throttle_reasons);

    mvprintw(row++, col, "PCIe    Gen%u x%u  (max Gen%u x%u)  ~%.0f MB/s each direction",
             g->pcie_gen, g->pcie_width, g->pcie_gen_max, g->pcie_width_max,
             g->pcie_capacity_MBps);

    double rx_pct = g->pcie_capacity_MBps > 0 ? 100.0 * g->rx_MBps / g->pcie_capacity_MBps : 0.0;
    double tx_pct = g->pcie_capacity_MBps > 0 ? 100.0 * g->tx_MBps / g->pcie_capacity_MBps : 0.0;
    double rx_avg = avg_hist(g->rx_hist, g->hist_count);
    double tx_avg = avg_hist(g->tx_hist, g->hist_count);

    snprintf(suf, sizeof(suf), "%.1f MB/s  avg %.1f  peak %.1f", g->rx_MBps, rx_avg, g->rx_peak);
    bar_line(row++, col, width, "PCIe RX", rx_pct, suf);

    snprintf(suf, sizeof(suf), "%.1f MB/s  avg %.1f  peak %.1f", g->tx_MBps, tx_avg, g->tx_peak);
    bar_line(row++, col, width, "PCIe TX", tx_pct, suf);
}

static void draw_ollama(int row, int col, int width, const OllamaStats *o) {
    (void)width;
    mvprintw(row++, col, "%-7s %s  API: %s  %s",
             o->unsloth ? "UNSLOTH" : (o->backend[0] ? o->backend : "BACKEND"),
             o->endpoint, o->api_ok ? "OK" : "DOWN", phase_name(o->phase));

    if (o->profile[0])
        mvprintw(row++, col, "Profile %s | pid %ld", o->profile, o->llama_pid);

    if (o->model_count == 0) {
        mvprintw(row++, col, "Model   none loaded");
    }

    for (int i = 0; i < o->model_count; i++) {
        const OllamaModel *m = &o->models[i];
        double gpu_pct = m->size_bytes && m->size_vram_bytes ?
            100.0 * (double)m->size_vram_bytes / (double)m->size_bytes : 0.0;

        mvprintw(row++, col, "Model   %s  %s  %s  [%s]",
                 m->name,
                 m->parameter_size[0] ? m->parameter_size : "?",
                 m->quantization[0] ? m->quantization : "?",
                 m->family[0] ? m->family : "?");

        mvprintw(row++, col,
                 "        size %.2f GiB | VRAM %s | GPU resident %s",
                 bytes_to_gib(m->size_bytes),
                 m->size_vram_bytes ? "tracked" : "NVML above",
                 m->size_vram_bytes ? (gpu_pct >= 99.5 ? "100%" : "partial") : "see layer target below");
    }

    const char *fa = o->flash_attn < 0 ? "?" : (o->flash_attn ? "ON" : "OFF");
    const OllamaModel *primary = o->model_count > 0 ? &o->models[0] : NULL;
    mvprintw(row++, col,
             "Context loaded=%llu | slot=%llu | model max=%llu | parallel=%d | batch=%d/%d",
             (unsigned long long)(primary ? primary->context_loaded : 0),
             (unsigned long long)o->slot_context,
             (unsigned long long)(primary ? primary->context_model_max : 0),
             o->parallel, o->batch_size, o->ubatch_size);

    mvprintw(row++, col, "KV      target K=%s V=%s | draft K=%s V=%s | FlashAttn=%s",
             o->kv_k[0] ? o->kv_k : "?",
             o->kv_v[0] ? o->kv_v : "?",
             o->kv_k_draft[0] ? o->kv_k_draft : "?",
             o->kv_v_draft[0] ? o->kv_v_draft : "?",
             fa);

    if (o->kv_total_mib > 0.0)
        mvprintw(row++, col, "KV size %.0f MiB (K %.0f / V %.0f)",
                 o->kv_total_mib, o->kv_k_mib, o->kv_v_mib);

    mvprintw(row++, col, "GPU     split mode=%s weights=%s | layers target=%s draft=%s",
             o->split_mode[0] ? o->split_mode : "?",
             o->layers_split[0] ? o->layers_split : "?",
             o->gpu_layers[0] ? o->gpu_layers : "?",
             o->gpu_layers_draft[0] ? o->gpu_layers_draft : "?");

    if (o->metric_draft_tokens > 0) {
        mvprintw(row++, col,
                 "Spec    %s | MTP max=%d | accept %.1f%% (%llu/%llu)",
                 o->spec_type[0] ? o->spec_type : "off",
                 o->spec_draft_n_max,
                 o->mtp_acceptance_pct,
                 (unsigned long long)o->metric_accepted_tokens,
                 (unsigned long long)o->metric_draft_tokens);
    } else {
        mvprintw(row++, col, "Spec    %s | MTP max=%d | accept n/a",
                 o->spec_type[0] ? o->spec_type : "off",
                 o->spec_draft_n_max);
    }

    if (o->layers_model > 0) {
        mvprintw(row++, col, "Offload %d/%d layers | split=%s",
                 o->layers_offload, o->layers_model,
                 o->layers_split[0] ? o->layers_split : "?");
    }

    uint64_t ctx_prompt = o->prompt_tokens ? o->prompt_tokens : o->prompt_total_est;
    uint64_t ctx_used = ctx_prompt + o->decoded_tokens;
    double ctx_pct = o->slot_context ? 100.0 * (double)ctx_used / (double)o->slot_context : 0.0;

    if (o->phase == PHASE_PREFILL) {
        double pp = o->prompt_progress * 100.0;
        mvprintw(row++, col,
                 "Prefill %.1f%% | processed %llu / total %llu | %.2f tok/s | slot ctx %llu",
                 pp,
                 (unsigned long long)o->prompt_processed_tokens,
                 (unsigned long long)o->prompt_total_est,
                 o->prompt_tps,
                 (unsigned long long)o->slot_context);
        mvprintw(row++, col, "Context %.1f%% used (%llu/%llu tokens)",
                 ctx_pct, (unsigned long long)ctx_used,
                 (unsigned long long)o->slot_context);
    } else if (o->phase == PHASE_GENERATING) {
        mvprintw(row++, col,
                 "Generate %llu tokens | %.2f tok/s | 3s %.2f tok/s | prefill %.2f tok/s",
                 (unsigned long long)o->decoded_tokens,
                 o->gen_tps, o->gen_tps_3s, o->last_prompt_tps);
        mvprintw(row++, col, "Context %.1f%% used (%llu/%llu) | prompt %llu, cached %llu",
                 ctx_pct, (unsigned long long)ctx_used,
                 (unsigned long long)o->slot_context,
                 (unsigned long long)o->prompt_tokens,
                 (unsigned long long)o->cached_prompt_tokens);
    } else {
        mvprintw(row++, col,
                 "Last    generation %.2f tok/s | prefill %.2f tok/s (%llu tok) | slot ctx %llu",
                 o->last_gen_tps, o->last_prompt_tps,
                 (unsigned long long)o->last_prompt_tokens,
                 (unsigned long long)o->slot_context);
    }

    mvprintw(row++, col, "Event   %s", o->last_event[0] ? o->last_event : "-");
}

static void usage(const char *argv0) {
    fprintf(stderr,
             "Usage: %s [-j] [-i milliseconds] [-u ollama_url] [-l llama_url]\n"
             "  -j  print one JSON snapshot and exit\n"
            "  -i  refresh interval (default 100 ms)\n"
            "  -u  force Ollama base URL\n"
            "  -l  force llama-server base URL\n"
            "      default: discover the active llama-server port, then Ollama :11434\n",
            argv0);
}

int main(int argc, char **argv) {
    setlocale(LC_ALL, "");
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    int interval_ms = 100;
    int json_output = 0;
    OllamaStats ollama = {
        .api_ok = 0,
        .phase = PHASE_UNKNOWN,
        .slot_id = -1,
        .task_id = -1,
        .flash_attn = -1,
        .parallel = 1,
    };
    snprintf(ollama.endpoint, sizeof(ollama.endpoint), "%s", "http://127.0.0.1:11434");
    snprintf(ollama.backend, sizeof(ollama.backend), "%s", "OLLAMA");
    char ollama_endpoint[256] = "http://127.0.0.1:11434";
    char llama_endpoint[256] = "http://127.0.0.1:11437";
    BackendMode backend_mode = BACKEND_AUTO;

    int opt;
    while ((opt = getopt(argc, argv, "ji:u:l:h")) != -1) {
        switch (opt) {
            case 'j':
                json_output = 1;
                break;
            case 'i':
                interval_ms = atoi(optarg);
                if (interval_ms < 50) interval_ms = 50;
                if (interval_ms > 2000) interval_ms = 2000;
                break;
            case 'u':
                backend_mode = BACKEND_OLLAMA;
                snprintf(ollama_endpoint, sizeof(ollama_endpoint), "%s", optarg);
                size_t l = strlen(ollama_endpoint);
                while (l > 0 && ollama_endpoint[l - 1] == '/')
                    ollama_endpoint[--l] = '\0';
                break;
            case 'l':
                backend_mode = BACKEND_LLAMA;
                snprintf(llama_endpoint, sizeof(llama_endpoint), "%s", optarg);
                size_t ll = strlen(llama_endpoint);
                while (ll > 0 && llama_endpoint[ll - 1] == '/')
                    llama_endpoint[--ll] = '\0';
                break;
            default:
                usage(argv[0]);
                return 1;
        }
    }

    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        fprintf(stderr, "curl_global_init failed\n");
        return 1;
    }

    select_backend(&ollama, backend_mode, ollama_endpoint,
                   llama_endpoint, sizeof(llama_endpoint));

    nvmlReturn_t nr = nvmlInit_v2();
    if (nr != NVML_SUCCESS) {
        fprintf(stderr, "NVML init failed: %s\n", nvmlErrorString(nr));
        curl_global_cleanup();
        return 1;
    }

    unsigned int gpu_count = 0;
    nr = nvmlDeviceGetCount_v2(&gpu_count);
    if (nr != NVML_SUCCESS || gpu_count == 0) {
        fprintf(stderr, "No NVIDIA GPUs found: %s\n", nvmlErrorString(nr));
        nvmlShutdown();
        curl_global_cleanup();
        return 1;
    }
    if (gpu_count > MAX_GPUS) gpu_count = MAX_GPUS;

    GpuStats gpu[MAX_GPUS];
    memset(gpu, 0, sizeof(gpu));

    for (unsigned int i = 0; i < gpu_count; i++) {
        nvmlDeviceGetHandleByIndex_v2(i, &gpu[i].h);
        nvmlDeviceGetName(gpu[i].h, gpu[i].name, sizeof(gpu[i].name));
    }

    sd_journal *journal = NULL;
    int journal_rc = init_journal(&journal, &ollama);

    if (strcmp(ollama.backend, "LLAMA") == 0) {
        llama_fetch_props(&ollama);
        llama_read_cmdline(&ollama);
        llama_fetch_profile(&ollama);
        llama_fetch_unsloth_log(&ollama);
        llama_fetch_slots(&ollama);
        llama_fetch_metrics(&ollama);
    }
    else ollama_fetch_ps(&ollama);

    if (json_output) {
        for (unsigned int i = 0; i < gpu_count; i++) gpu_update(&gpu[i], 1);
        print_json_snapshot(gpu, gpu_count, &ollama);
        if (journal) sd_journal_close(journal);
        nvmlShutdown();
        curl_global_cleanup();
        return 0;
    }

    initscr();
    cbreak();
    noecho();
    curs_set(0);
    keypad(stdscr, TRUE);
    nodelay(stdscr, TRUE);

    if (has_colors()) {
        start_color();
        use_default_colors();
        init_pair(1, COLOR_GREEN, -1);
        init_pair(2, COLOR_YELLOW, -1);
        init_pair(3, COLOR_RED, -1);
        init_pair(4, COLOR_CYAN, -1);
    }

    int hist_target = 1000 / interval_ms;
    if (hist_target < 1) hist_target = 1;
    if (hist_target > HIST_MAX) hist_target = HIST_MAX;

    uint64_t tick = 0;
    int ps_every = 1000 / interval_ms;
    if (ps_every < 1) ps_every = 1;

    while (!g_stop) {
        int ch = getch();
        if (ch == 'q' || ch == 'Q') break;
        int source_changed = 0;
        if (ch == 't' || ch == 'T') {
            backend_mode = (BackendMode)((backend_mode + 1) % 3);
            source_changed = select_backend(&ollama, backend_mode,
                                            ollama_endpoint,
                                            llama_endpoint, sizeof(llama_endpoint));
        }
        if (ch == 'r' || ch == 'R') {
            for (unsigned int i = 0; i < gpu_count; i++) {
                gpu[i].rx_peak = gpu[i].tx_peak = 0.0;
                gpu[i].hist_count = gpu[i].hist_pos = 0;
                memset(gpu[i].rx_hist, 0, sizeof(gpu[i].rx_hist));
                memset(gpu[i].tx_hist, 0, sizeof(gpu[i].tx_hist));
            }
        }

        if (backend_mode == BACKEND_AUTO &&
            (tick % (uint64_t)ps_every) == 0) {
            source_changed |= select_backend(&ollama, backend_mode,
                                             ollama_endpoint,
                                             llama_endpoint, sizeof(llama_endpoint));
        }

        if (source_changed) {
            if (journal) sd_journal_close(journal);
            journal = NULL;
            journal_rc = init_journal(&journal, &ollama);
        }

        poll_journal(journal, &ollama);

        if ((tick % (uint64_t)ps_every) == 0) {
            if (strcmp(ollama.backend, "LLAMA") == 0) {
                llama_fetch_props(&ollama);
                llama_read_cmdline(&ollama);
                llama_fetch_profile(&ollama);
                llama_fetch_unsloth_log(&ollama);
                llama_fetch_slots(&ollama);
                llama_fetch_metrics(&ollama);
            } else {
                ollama_fetch_ps(&ollama);
            }
        }

        for (unsigned int i = 0; i < gpu_count; i++)
            gpu_update(&gpu[i], hist_target);

        erase();

        int rows, cols;
        getmaxyx(stdscr, rows, cols);
        int width = cols - 2;
        if (width > 140) width = 140;

        if (has_colors()) attron(COLOR_PAIR(4) | A_BOLD);
        mvprintw(0, 1, "gpumon + %s   source=%s   refresh=%dms   q=quit r=reset t=source",
                 strcmp(ollama.backend, "LLAMA") == 0 ? "llama-server" : "ollama",
                 backend_mode_name(backend_mode), interval_ms);
        if (has_colors()) attroff(COLOR_PAIR(4) | A_BOLD);

        int gpu_columns = (cols >= 100 && gpu_count > 1) ? 2 : 1;
        int gpu_width = gpu_columns == 2 ? (cols - 3) / 2 : width;
        if (gpu_width > 70) gpu_width = 70;
        for (unsigned int i = 0; i < gpu_count; i++) {
            int gpu_row = 2 + (int)(i / (unsigned int)gpu_columns) * 10;
            int gpu_col = 1 + (int)(i % (unsigned int)gpu_columns) * (gpu_width + 3);
            if (gpu_row + 10 >= rows) break;
            draw_gpu(gpu_row, gpu_col, gpu_width, i, &gpu[i]);
        }
        int gpu_rows = ((int)gpu_count + gpu_columns - 1) / gpu_columns;
        int row = 2 + gpu_rows * 10;

        if (row + 11 < rows) {
            mvhline(row++, 1, ACS_HLINE, width > 1 ? width - 1 : 1);
            draw_ollama(row, 1, width, &ollama);
            row += 11;
        }

        if (journal_rc < 0 && row < rows - 1) {
            mvprintw(rows - 2, 1,
                     "journal: unavailable (%s) -> live TPS/KV log metrics disabled",
                     strerror(-journal_rc));
        }

        refresh();
        tick++;
        usleep((useconds_t)interval_ms * 1000);
    }

    endwin();
    if (journal) sd_journal_close(journal);
    nvmlShutdown();
    curl_global_cleanup();
    return 0;
}
