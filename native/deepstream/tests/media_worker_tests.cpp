#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <cassert>
#include <chrono>
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
  const auto t0 = Clock::time_point{};
  MediaWorker worker;
  assert(worker.add_camera(CameraConfig{"cam-a", "r7", 2, 1000ms, 100ms, 2s}));
  assert(!worker.add_camera(CameraConfig{"cam-a", "r7", 2, 1000ms, 100ms, 2s}));
  assert(worker.ingest("cam-a", frame(), t0));
  assert(worker.ingest("cam-a", frame("track-2"), t0 + 1ms));
  assert(worker.ingest("cam-a", frame("track-3"), t0 + 2ms));
  auto health = worker.health(t0 + 2ms);
  assert(health.size() == 1);
  assert(health[0].state == StreamState::healthy);
  assert(health[0].queue_depth == 2);
  assert(health[0].frames_dropped == 1);
  assert(worker.health(t0 + 2000ms)[0].reason == "freshness_deadline");
  worker.mark_reconnect_attempt("cam-a", t0 + 2001ms);
  assert(worker.health(t0 + 2002ms)[0].state == StreamState::reconnecting);
  assert(worker.health(t0 + 2002ms)[0].reconnect_delay == 100ms);
  worker.mark_reconnect_attempt("cam-a", t0 + 2003ms);
  assert(worker.health(t0 + 2003ms)[0].reconnect_delay == 200ms);
  auto candidate = worker.candidate("cam-a", frame(), t0 + 3ms);
  assert(candidate.has_value());
  const std::string json = candidate->to_json();
  assert(json.find("track.candidate.v1") != std::string::npos);
  assert(json.find("raw_pixels") == std::string::npos);
  assert(json.find("cam-a") != std::string::npos);
  assert(!worker.ingest("missing", frame(), t0));
  DeepStreamPipeline pipeline;
  assert(!pipeline.available());
  std::string error;
  assert(!pipeline.start(&error));
  assert(error.find("reference profile") != std::string::npos);
  CandidatePublisher publisher(2);
  assert(publisher.enqueue(*candidate));
  assert(publisher.enqueue(*candidate));
  assert(publisher.enqueue(*candidate));
  assert(publisher.pending() == 2);
  assert(publisher.dropped() == 1);
  assert(publisher.flush([](const auto&) { return false; }) == 0);
  assert(publisher.pending() == 2);
  assert(publisher.publish_failures() == 1);
  assert(publisher.flush([](const auto&) { return true; }) == 2);
  assert(publisher.pending() == 0);
  return 0;
}
