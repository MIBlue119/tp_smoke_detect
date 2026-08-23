#pragma once

#include "tp_smoke_detect/media/media_worker.hpp"

#include <string>

namespace tp_smoke_detect::media {

struct DeepStreamPipelineConfig {
  std::size_t max_cameras{20};
  std::size_t batch_size{20};
  std::uint64_t batched_push_timeout_us{40000};
  bool live_source{true};
};

class DeepStreamPipeline {
 public:
  explicit DeepStreamPipeline(DeepStreamPipelineConfig config = {});
  [[nodiscard]] bool available() const;
  [[nodiscard]] bool start(std::string* error = nullptr);
  void stop();

 private:
  DeepStreamPipelineConfig config_;
  bool running_{false};
};

}  // namespace tp_smoke_detect::media
