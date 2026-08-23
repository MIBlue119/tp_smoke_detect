#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <chrono>
#include <iostream>
#include <string>

using namespace std::chrono_literals;
using tp_smoke_detect::media::CameraConfig;
using tp_smoke_detect::media::CandidatePublisher;
using tp_smoke_detect::media::Clock;
using tp_smoke_detect::media::DeepStreamPipeline;
using tp_smoke_detect::media::FrameSample;
using tp_smoke_detect::media::MediaWorker;
using tp_smoke_detect::media::StreamState;

namespace {
FrameSample frame(const char* track = "track-1") {
  return FrameSample{100, 200, 300, 1920, 1080, track, {.1, .2, .3, .4, .95}};
}
}

int main() {
#define CHECK(condition) \
  do { \
    if (!(condition)) { \
      std::cerr << "check failed: " #condition << std::endl; \
      return 1; \
    } \
  } while (false)
  const auto t0 = Clock::time_point{};
  MediaWorker worker;
  CHECK(worker.add_camera(CameraConfig{"cam-a", "r7", 2, 1000ms, 100ms, 2s}));
  CHECK(!worker.add_camera(CameraConfig{"cam-a", "r7", 2, 1000ms, 100ms, 2s}));
  CHECK(worker.ingest("cam-a", frame(), t0));
  CHECK(worker.ingest("cam-a", frame("track-2"), t0 + 1ms));
  CHECK(worker.ingest("cam-a", frame("track-3"), t0 + 2ms));
  auto health = worker.health(t0 + 2ms);
  CHECK(health.size() == 1);
  CHECK(health[0].state == StreamState::healthy);
  CHECK(health[0].queue_depth == 2);
  CHECK(health[0].frames_dropped == 1);
  CHECK(worker.health(t0 + 2000ms)[0].reason == "freshness_deadline");
  worker.mark_reconnect_attempt("cam-a", t0 + 2001ms);
  CHECK(worker.health(t0 + 2002ms)[0].state == StreamState::reconnecting);
  CHECK(worker.health(t0 + 2002ms)[0].reconnect_delay == 100ms);
  worker.mark_reconnect_attempt("cam-a", t0 + 2003ms);
  CHECK(worker.health(t0 + 2003ms)[0].reconnect_delay == 200ms);
  auto candidate = worker.candidate("cam-a", frame(), t0 + 3ms);
  CHECK(candidate.has_value());
  const std::string json = candidate->to_json();
  CHECK(json.find("track.candidate.v1") != std::string::npos);
  CHECK(json.find("\"event_id\":\"") != std::string::npos);
  CHECK(json.find("\"correlation_id\":\"") != std::string::npos);
  CHECK(json.find("\"producer\":\"tp-smoke-detect.native-reference\"") !=
        std::string::npos);
  CHECK(json.find("\"occurred_at\":\"") != std::string::npos);
  CHECK(json.find("raw_pixels") == std::string::npos);
  CHECK(json.find("cam-a") != std::string::npos);
  CHECK(!worker.ingest("missing", frame(), t0));
  DeepStreamPipeline pipeline;
  CHECK(!pipeline.available());
  std::string error;
  CHECK(!pipeline.start(&error));
  CHECK(error.find("reference profile") != std::string::npos);
  CandidatePublisher publisher(2);
  CHECK(publisher.enqueue(*candidate));
  CHECK(publisher.enqueue(*candidate));
  CHECK(publisher.enqueue(*candidate));
  CHECK(publisher.pending() == 2);
  CHECK(publisher.dropped() == 1);
  CHECK(publisher.flush([](const auto&) { return false; }) == 0);
  CHECK(publisher.pending() == 2);
  CHECK(publisher.publish_failures() == 1);
  CHECK(publisher.flush([](const auto&) { return true; }) == 2);
  CHECK(publisher.pending() == 0);
  std::cout << json << std::endl;
#undef CHECK
  return 0;
}
