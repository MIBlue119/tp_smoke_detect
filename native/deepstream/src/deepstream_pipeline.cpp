#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <string>

#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
#include <gst/gst.h>
#endif

namespace tp_smoke_detect::media {

DeepStreamPipeline::DeepStreamPipeline(DeepStreamPipelineConfig config) : config_(config) {}

bool DeepStreamPipeline::available() const {
#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  return config_.max_cameras > 0 && config_.batch_size > 0;
#else
  return false;
#endif
}

bool DeepStreamPipeline::start(std::string* error) {
  if (!available()) {
    if (error != nullptr) *error = "DeepStream adapter is not compiled; use the reference profile";
    return false;
  }
#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  int argument_count = 0;
  gst_init(&argument_count, nullptr);
  running_ = true;
  return true;
#endif
  return false;
}

void DeepStreamPipeline::stop() { running_ = false; }

}  // namespace tp_smoke_detect::media
