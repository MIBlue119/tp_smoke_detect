#pragma once

#include "tp_smoke_detect/media/candidate.hpp"

#include <chrono>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace tp_smoke_detect::media {

using Clock = std::chrono::steady_clock;

struct FrameSample {
  std::uint64_t source_pts_ns{0};
  std::uint64_t capture_ts_ns{0};
  std::uint64_t received_ts_ns{0};
  std::int32_t width{0};
  std::int32_t height{0};
  std::string track_id;
  BoundingBox person_box;
};

struct CameraConfig {
  std::string camera_id;
  std::string revision;
  std::size_t queue_capacity{32};
  std::chrono::milliseconds freshness_timeout{3000};
  std::chrono::milliseconds reconnect_initial{250};
  std::chrono::milliseconds reconnect_max{10000};
};

enum class StreamState { healthy, degraded, reconnecting };

struct CameraHealth {
  std::string camera_id;
  StreamState state{StreamState::degraded};
  std::size_t queue_depth{0};
  std::uint64_t frames_received{0};
  std::uint64_t frames_dropped{0};
  std::uint64_t reconnect_attempts{0};
  std::chrono::milliseconds reconnect_delay{0};
  std::optional<Clock::time_point> last_good_frame;
  std::string reason{"not_started"};
};

class CameraStream {
 public:
  explicit CameraStream(CameraConfig config);

  bool enqueue(FrameSample sample, Clock::time_point now);
  [[nodiscard]] std::optional<FrameSample> pop();
  [[nodiscard]] CameraHealth health(Clock::time_point now) const;
  void mark_reconnect_attempt(Clock::time_point now);
  void mark_reconnected(Clock::time_point now);
  [[nodiscard]] std::chrono::milliseconds reconnect_delay() const;
  [[nodiscard]] const CameraConfig& config() const { return config_; }

 private:
  CameraConfig config_;
  std::deque<FrameSample> queue_;
  std::uint64_t frames_received_{0};
  std::uint64_t frames_dropped_{0};
  std::uint64_t reconnect_attempts_{0};
  std::optional<Clock::time_point> last_good_frame_;
  std::optional<Clock::time_point> reconnect_started_;
};

class MediaWorker {
 public:
  bool add_camera(CameraConfig config);
  bool ingest(const std::string& camera_id, FrameSample sample,
              Clock::time_point now = Clock::now());
  [[nodiscard]] std::optional<FrameSample> next(const std::string& camera_id);
  void mark_reconnect_attempt(const std::string& camera_id,
                              Clock::time_point now = Clock::now());
  void mark_reconnected(const std::string& camera_id,
                        Clock::time_point now = Clock::now());
  [[nodiscard]] std::vector<CameraHealth> health(Clock::time_point now = Clock::now()) const;
  [[nodiscard]] std::optional<CandidateEnvelope> candidate(
      const std::string& camera_id, const FrameSample& sample,
      Clock::time_point now = Clock::now()) const;

 private:
  std::map<std::string, CameraStream> cameras_;
};

// Broker-neutral bounded handoff. The DeepStream adapter can provide the
// transport callback without making MQTT or another broker a native dependency.
class CandidatePublisher {
 public:
  explicit CandidatePublisher(std::size_t max_pending = 128);

  bool enqueue(CandidateEnvelope candidate);
  // Sends FIFO candidates until the first transport failure. Failed candidates
  // remain queued for a later bounded retry; host memory never grows without
  // the configured limit.
  std::size_t flush(const std::function<bool(const CandidateEnvelope&)>& send);
  [[nodiscard]] std::size_t pending() const { return pending_.size(); }
  [[nodiscard]] std::uint64_t dropped() const { return dropped_; }
  [[nodiscard]] std::uint64_t publish_failures() const { return publish_failures_; }

 private:
  std::size_t max_pending_;
  std::deque<CandidateEnvelope> pending_;
  std::uint64_t dropped_{0};
  std::uint64_t publish_failures_{0};
};

[[nodiscard]] const char* to_string(StreamState state);

}  // namespace tp_smoke_detect::media
