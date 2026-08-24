#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <unordered_map>
#include <unistd.h>
#include <vector>

using namespace tp_smoke_detect::media;

namespace {

std::string trim(std::string value) {
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) return {};
  const auto last = value.find_last_not_of(" \t\r\n");
  value = value.substr(first, last - first + 1);
  if (value.size() >= 2 && value.front() == '"' && value.back() == '"') {
    value = value.substr(1, value.size() - 2);
  }
  return value;
}

std::unordered_map<std::string, std::string> read_config(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot read media config: " + path);
  std::unordered_map<std::string, std::string> values;
  std::string section;
  std::string line;
  while (std::getline(input, line)) {
    const auto comment = line.find('#');
    if (comment != std::string::npos) line.resize(comment);
    line = trim(line);
    if (line.empty()) continue;
    if (line.front() == '[' && line.back() == ']') {
      section = trim(line.substr(1, line.size() - 2));
      continue;
    }
    const auto equals = line.find('=');
    const auto colon = line.find(':');
    const auto delimiter = equals == std::string::npos
                               ? colon
                               : (colon == std::string::npos ? equals : std::min(equals, colon));
    if (delimiter == std::string::npos) continue;
    auto key = trim(line.substr(0, delimiter));
    auto value = trim(line.substr(delimiter + 1));
    if (key.empty() || value.empty()) continue;
    values[key] = value;
    // The deployment file is a normal DeepStream INI document.  These
    // aliases keep the broker publisher bound to the same source/GIE config
    // that DeepStream loads while the tp-smoke-detect section supplies the
    // three explicit GPU plugin paths and immutable revisions.
    if (section == "source0" && key == "uri") values["source_uri"] = value;
    if (section == "primary-gie" && key == "config-file") values["detector_config"] = value;
    if (section == "tracker" && key == "ll-config-file") values["tracker_config"] = value;
  }
  return values;
}

std::string resolve(std::string value) {
  constexpr std::string_view prefix = "env:";
  if (value.starts_with(prefix)) {
    const char* resolved = std::getenv(value.substr(prefix.size()).c_str());
    return resolved == nullptr ? std::string{} : std::string{resolved};
  }
  return value;
}

std::string required(const std::unordered_map<std::string, std::string>& values,
                     const std::string& key) {
  const auto found = values.find(key);
  const auto value = found == values.end() ? std::string{} : resolve(found->second);
  if (value.empty() || value == "unresolved" || value == "unknown") {
    throw std::runtime_error("media config requires bound " + key);
  }
  return value;
}

std::string value_or(const std::unordered_map<std::string, std::string>& values,
                     const std::string& key, const std::string& fallback) {
  const auto found = values.find(key);
  return found == values.end() ? fallback : resolve(found->second);
}

bool publish_qos1(const std::string& host, const std::string& port, const std::string& topic,
                  const std::string& payload) {
  std::vector<std::string> arguments = {"mosquitto_pub", "-h", host, "-p", port, "-t", topic,
                                        "-q", "1", "-m", payload};
  std::vector<char*> argv;
  argv.reserve(arguments.size() + 1);
  for (auto& argument : arguments) argv.push_back(argument.data());
  argv.push_back(nullptr);
  const pid_t child = fork();
  if (child < 0) return false;
  if (child == 0) {
    execvp(argv[0], argv.data());
    _exit(127);
  }
  int status = 0;
  if (waitpid(child, &status, 0) < 0) return false;
  return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

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

DeepStreamPipelineConfig pipeline_config(const std::unordered_map<std::string, std::string>& values) {
  DeepStreamPipelineConfig config;
  config.detector_config = required(values, "detector_config");
  config.tracker_config = required(values, "tracker_config");
  config.pose_model_path = required(values, "pose_model_path");
  config.hand_model_path = required(values, "hand_model_path");
  config.crop_infer_config = required(values, "crop_infer_config");
  config.artifact_revision = required(values, "artifact_revision");
  const auto manifest = required(values, "manifest_revision");
  config.detector_model_revision = value_or(values, "detector_model_revision", manifest + ":person_detector");
  config.pose_model_revision = value_or(values, "pose_model_revision", manifest + ":pose_landmarker");
  config.hand_model_revision = value_or(values, "hand_model_revision", manifest + ":hand_landmarker");
  config.crop_model_revision = value_or(values, "crop_model_revision", manifest + ":crop_classifier");
  config.sources.push_back(DeepStreamSourceConfig{
      .camera_id = required(values, "camera_id"),
      .uri = required(values, "source_uri"),
      .camera_config_revision = required(values, "camera_config_revision"),
      .source_id = 0,
  });
  return config;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc > 1 && std::string(argv[1]) == "--reference") return reference_once();
  if (argc != 3 || std::string(argv[1]) != "--config") {
    std::cerr << "usage: media_publisher --config <bound-runtime-config>\n";
    return 78;
  }
  try {
    const auto values = read_config(argv[2]);
    auto config = pipeline_config(values);
    const auto host = value_or(values, "broker_host", "127.0.0.1");
    const auto port = value_or(values, "broker_port", "1883");
    const auto topic = required(values, "candidate_topic");
    DeepStreamPipeline pipeline(std::move(config));
    pipeline.set_candidate_callback([&](const CandidateEnvelope& candidate) {
      const auto payload = candidate.to_json();
      const bool delivered = publish_qos1(host, port, topic, payload);
      if (!delivered) {
        std::cerr << "MQTT QoS1 publication failed for event " << candidate.event_id << '\n';
      }
      return delivered;
    });
    std::string error;
    if (!pipeline.start(&error)) {
      std::cerr << error << '\n';
      return 78;
    }
    while (pipeline.running()) std::this_thread::sleep_for(std::chrono::seconds(1));
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 78;
  }
}
