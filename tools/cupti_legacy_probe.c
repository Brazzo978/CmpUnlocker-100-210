// SPDX-License-Identifier: GPL-2.0-only
#include <cuda.h>
#include <cupti.h>
#include <cupti_events.h>
#include <cupti_metrics.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static const char *cupti_result(CUptiResult result) {
    const char *text = NULL;
    (void) cuptiGetResultString(result, &text);
    return text ? text : "unknown";
}

static int probe_device(CUdevice device, int ordinal) {
    char name[128] = {0};
    if (cuDeviceGetName(name, sizeof(name), device) != CUDA_SUCCESS) {
        fprintf(stderr, "device[%d]: cuDeviceGetName failed\n", ordinal);
        return 1;
    }

    uint32_t domain_count = 0;
    CUptiResult result = cuptiDeviceGetNumEventDomains(device, &domain_count);
    printf("device[%d] name=%s eventDomains rc=%d %s count=%u\n",
           ordinal, name, result, cupti_result(result), domain_count);
    if (result != CUPTI_SUCCESS || domain_count == 0) return 1;

    size_t domain_bytes = (size_t) domain_count * sizeof(CUpti_EventDomainID);
    CUpti_EventDomainID *domains = calloc(domain_count, sizeof(*domains));
    if (!domains) {
        fprintf(stderr, "device[%d]: event-domain allocation failed\n", ordinal);
        return 1;
    }
    result = cuptiDeviceEnumEventDomains(device, &domain_bytes, domains);
    printf("device[%d] enumEventDomains rc=%d %s bytes=%zu\n",
           ordinal, result, cupti_result(result), domain_bytes);
    free(domains);
    if (result != CUPTI_SUCCESS) return 1;

    uint32_t metric_count = 0;
    result = cuptiDeviceGetNumMetrics(device, &metric_count);
    printf("device[%d] metrics rc=%d %s count=%u\n",
           ordinal, result, cupti_result(result), metric_count);
    if (result != CUPTI_SUCCESS || metric_count == 0) return 1;

    size_t metric_bytes = (size_t) metric_count * sizeof(CUpti_MetricID);
    CUpti_MetricID *metrics = calloc(metric_count, sizeof(*metrics));
    if (!metrics) {
        fprintf(stderr, "device[%d]: metric allocation failed\n", ordinal);
        return 1;
    }
    result = cuptiDeviceEnumMetrics(device, &metric_bytes, metrics);
    printf("device[%d] enumMetrics rc=%d %s bytes=%zu\n",
           ordinal, result, cupti_result(result), metric_bytes);
    if (result != CUPTI_SUCCESS) {
        free(metrics);
        return 1;
    }

    int failed = 0;
    const size_t returned = metric_bytes / sizeof(*metrics);
    for (size_t i = 0; i < returned; ++i) {
        char metric_name[256] = {0};
        size_t name_size = sizeof(metric_name);
        CUptiResult attr_result = cuptiMetricGetAttribute(
            metrics[i], CUPTI_METRIC_ATTR_NAME, &name_size, metric_name);
        if (attr_result == CUPTI_SUCCESS) {
            printf("device[%d] metric[%zu] name=%s\n", ordinal, i, metric_name);
        } else {
            fprintf(stderr, "device[%d] metric[%zu] name rc=%d %s\n",
                    ordinal, i, attr_result, cupti_result(attr_result));
            failed = 1;
        }
    }
    free(metrics);
    return failed;
}

int main(void) {
    uint32_t runtime_version = 0;
    CUptiResult version_result = cuptiGetVersion(&runtime_version);
    printf("cuptiRuntimeVersion rc=%d %s version=%u compileApiVersion=%u\n",
           version_result, cupti_result(version_result), runtime_version,
           (unsigned) CUPTI_API_VERSION);
    if (version_result != CUPTI_SUCCESS) return 1;

    CUresult cuda_result = cuInit(0);
    if (cuda_result != CUDA_SUCCESS) {
        fprintf(stderr, "cuInit=%d\n", cuda_result);
        return 2;
    }

    int device_count = 0;
    cuda_result = cuDeviceGetCount(&device_count);
    if (cuda_result != CUDA_SUCCESS || device_count <= 0) {
        fprintf(stderr, "cuDeviceGetCount=%d count=%d\n", cuda_result, device_count);
        return 2;
    }

    int failed = 0;
    for (int ordinal = 0; ordinal < device_count; ++ordinal) {
        CUdevice device;
        cuda_result = cuDeviceGet(&device, ordinal);
        if (cuda_result != CUDA_SUCCESS) {
            fprintf(stderr, "device[%d]: cuDeviceGet=%d\n", ordinal, cuda_result);
            failed = 1;
            continue;
        }
        failed |= probe_device(device, ordinal);
    }

    return failed ? 1 : 0;
}
