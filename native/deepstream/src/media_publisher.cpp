#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

using namespace tp_smoke_detect::media;

namespace {
int reference_once() {
  MediaWorker worker;
  if (!worker.add_camera(CameraConfig{"camera-reference", "camera-r1"})) return 78;
  FrameSample sample{100, 200, 300, 1920, 1080, "track-reference", {0.1, 0.2, 0.3, 0.4, 0.95}};
  if (!worker.ingest("camera-reference", sample)) return 78;
  auto candidate = worker.candidate("camera-reference", sample);
  if (!candidate) return 78;
  std::cout << candidate->to_json() << '\n';
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  if (argc > 1 && std::string(argv[1]) == "--reference") return reference_once();

  DeepStreamPipelineConfig config;
  config.detector_config = "/opt/smoke-detect/deepstream/config/peoplenet-transformer.txt";
  config.tracker_config = "/opt/smoke-detect/deepstream/config/nvdcf.txt";
  config.pose_model_path = "/models/mediapipe-pose-landmarker/pose_landmarker.task";
  config.hand_model_path = "/models/mediapipe-hand-landmarker/hand_landmarker.task";
  config.crop_infer_config = "/opt/smoke-detect/deepstream/config/siglip2-crop-nvinferserver.pbtxt";
  config.sources.push_back(DeepStreamSourceConfig{
      .camera_id = std::getenv("SMOKE_GPU_CAMERA_ID") ? std::getenv("SMOKE_GPU_CAMERA_ID")
                                                       : "camera-gpu-01",
      .uri = std::getenv("SMOKE_GPU_SOURCE_URI") ? std::getenv("SMOKE_GPU_SOURCE_URI")
                                                  : "file:///media/replay/stream-01.mp4",
      .camera_config_revision = "gpu-runtime-r1",
      .source_id = 0,
  });
  DeepStreamPipeline pipeline(config);
  pipeline.set_candidate_callback([](const CandidateEnvelope& candidate) {
    std::cout << candidate.to_json() << std::endl;
    return true;
  });
  std::string error;
  if (!pipeline.start(&error)) {
    std::cerr << error << '\n';
    return 78;
  }
  while (pipeline.running()) std::this_thread::sleep_for(std::chrono::seconds(1));
  return 0;
}
