#pragma once

#include "tp_smoke_detect/media/media_worker.hpp"

#include <functional>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace tp_smoke_detect::media {

struct DeepStreamSourceConfig {
  std::string camera_id;
  std::string uri;
  std::string camera_config_revision{"unresolved"};
  std::uint32_t source_id{0};
  std::uint32_t latency_ms{200};
  std::uint32_t reconnect_interval_s{10};
  std::uint32_t reconnect_attempts{3};
};

struct DeepStreamPipelineConfig {
  std::size_t max_cameras{20};
  std::size_t batch_size{20};
  std::uint64_t batched_push_timeout_us{40000};
  bool live_source{true};
  std::string detector_config{"native/deepstream/config/peoplenet-transformer.txt"};
  std::string tracker_config{"native/deepstream/config/nvdcf.txt"};
  std::string pose_model_path{"model-repository/mediapipe-pose-landmarker/pose_landmarker.task"};
  std::string hand_model_path{"model-repository/mediapipe-hand-landmarker/hand_landmarker.task"};
  std::string crop_infer_config{"native/deepstream/config/siglip2-crop-nvinferserver.pbtxt"};
  std::size_t max_pending_candidates{128};
  std::vector<DeepStreamSourceConfig> sources;
};

struct DeepStreamReadiness {
  bool compiled{false};
  bool runtime_present{false};
  bool graph_constructed{false};
  bool qualified{false};
  std::string state{"unavailable"};
  std::string reason{"deepstream_adapter_not_compiled"};
};

class DeepStreamPipeline {
 public:
  using CandidateCallback = std::function<bool(const CandidateEnvelope&)>;

  explicit DeepStreamPipeline(DeepStreamPipelineConfig config = {});
  ~DeepStreamPipeline();

  DeepStreamPipeline(const DeepStreamPipeline&) = delete;
  DeepStreamPipeline& operator=(const DeepStreamPipeline&) = delete;

  bool add_source(DeepStreamSourceConfig source);
  void set_candidate_callback(CandidateCallback callback);
  [[nodiscard]] bool available() const;
  [[nodiscard]] bool start(std::string* error = nullptr);
  void stop();
  [[nodiscard]] bool running() const { return running_; }
  [[nodiscard]] DeepStreamReadiness readiness() const { return readiness_; }
  [[nodiscard]] std::string graph_description() const;
  [[nodiscard]] std::vector<CameraHealth> camera_health(Clock::time_point now = Clock::now()) const;
  [[nodiscard]] const CandidatePublisher& publisher() const { return publisher_; }

  // Opaque callback seams keep the public header free of proprietary SDK
  // types while allowing the GStreamer callbacks to delegate to this owner.
  void on_metadata_buffer(void* buffer);
  void on_source_pad(void* source_element, void* pad);
  void on_bus_message(void* message);

 private:
  struct RuntimeState;

  bool validate_config(std::string* error) const;
  void set_failure(std::string reason);

  DeepStreamPipelineConfig config_;
  MediaWorker worker_;
  CandidatePublisher publisher_;
  CandidateCallback candidate_callback_;
  DeepStreamReadiness readiness_;
  std::unique_ptr<RuntimeState> runtime_;
  bool running_{false};
};

}  // namespace tp_smoke_detect::media
