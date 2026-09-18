#include <cuda.h>
#include <cupti.h>
#include <cupti_events.h>
#include <cupti_metrics.h>
#include <stdio.h>
#include <stdlib.h>

static const char *cr(CUptiResult r) { const char *s = 0; cuptiGetResultString(r, &s); return s ? s : "unknown"; }
int main(void) {
  CUdevice d; CUresult cu = cuInit(0); if (cu != CUDA_SUCCESS) { printf("cuInit=%d\n", cu); return 2; }
  if (cuDeviceGet(&d, 0) != CUDA_SUCCESS) { puts("cuDeviceGet failed"); return 2; }
  char name[128]; cuDeviceGetName(name, sizeof(name), d); printf("device=%s ordinal=0\n", name);
  uint32_t nd = 0, nm = 0; CUptiResult r = cuptiDeviceGetNumEventDomains(d, &nd);
  printf("eventDomains rc=%d %s count=%u\n", r, cr(r), nd);
  r = cuptiDeviceGetNumMetrics(d, &nm);
  printf("metrics rc=%d %s count=%u\n", r, cr(r), nm);
  if (r == CUPTI_SUCCESS && nm) {
    size_t sz = nm * sizeof(CUpti_MetricID); CUpti_MetricID *ids = calloc(nm, sizeof(*ids));
    r = cuptiDeviceEnumMetrics(d, &sz, ids); printf("enumMetrics rc=%d %s bytes=%zu\n", r, cr(r), sz);
    for (unsigned i=0; r==CUPTI_SUCCESS && i<sz/sizeof(*ids); ++i) { char n[256]={0}; size_t ns=sizeof(n); CUptiResult q=cuptiMetricGetAttribute(ids[i], CUPTI_METRIC_ATTR_NAME, &ns, n); if (q==CUPTI_SUCCESS) printf("metric[%u] name=%s\n", i,n); }
    free(ids);
  }
  return 0;
}
