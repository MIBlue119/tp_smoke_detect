#include "tp_smoke_detect/media/media_worker.hpp"

#include <algorithm>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace tp_smoke_detect::media {
namespace {

std::uint64_t fnv1a(const std::string& value, std::uint64_t seed) {
  std::uint64_t hash = seed;
  for (const unsigned char character : value) {
    hash ^= character;
    hash *= 1099511628211ULL;
  }
  return hash;
}

std::string stable_uuid(const std::string& seed) {
  auto most = fnv1a(seed, 1469598103934665603ULL);
  auto least = fnv1a(seed + "\\0tp-smoke-detect", 1099511628211ULL);
  most = (most & 0xffffffffffff0fffULL) | 0x0000000000005000ULL;
  least = (least & 0x3fffffffffffffffULL) | 0x8000000000000000ULL;
  std::ostringstream output;
  output << std::hex << std::setfill('0') << std::setw(8) << (most >> 32) << '-'
         << std::setw(4) << ((most >> 16) & 0xffffULL) << '-'
         << std::setw(4) << (most & 0xffffULL) << '-'
         << std::setw(4) << (least >> 48) << '-' << std::setw(12)
         << (least & 0xffffffffffffULL);
  return output.str();
}

std::string utc_timestamp(std::uint64_t timestamp_ns) {
  const auto seconds = static_cast<std::time_t>(timestamp_ns / 1'000'000'000ULL);
  std::tm utc{};
#if defined(_WIN32)
  gmtime_s(&utc, &seconds);
#else
  gmtime_r(&seconds, &utc);
#endif
  std::ostringstream result;
  result << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
  return result.str();
}

}  // namespace

CameraStream::CameraStream(CameraConfig config) : config_(std::move(config)) {
  if (config_.camera_id.empty()) throw std::invalid_argument("camera_id is required");
  if (config_.revision.empty()) throw std::invalid_argument("camera revision is required");
  if (config_.queue_capacity == 0) throw std::invalid_argument("queue capacity must be positive");
  if (config_.reconnect_initial <= std::chrono::milliseconds{0} ||
      config_.reconnect_initial > config_.reconnect_max) {
    throw std::invalid_argument("reconnect backoff bounds are invalid");
  }
}

bool CameraStream::enqueue(FrameSample sample, Clock::time_point now) {
  if (sample.width <= 0 || sample.height <= 0 || sample.track_id.empty()) {
    return false;
  }
  ++frames_received_;
  last_good_frame_ = now;
  reconnect_started_.reset();
  if (queue_.size() >= config_.queue_capacity) {
    queue_.pop_front();
    ++frames_dropped_;
  }
  queue_.push_back(std::move(sample));
  return true;
}

std::optional<FrameSample> CameraStream::pop() {
  if (queue_.empty()) return std::nullopt;
  FrameSample result = std::move(queue_.front());
  queue_.pop_front();
  return result;
}

CameraHealth CameraStream::health(Clock::time_point now) const {
  CameraHealth result{config_.camera_id, StreamState::degraded, queue_.size(), frames_received_,
                      frames_dropped_, reconnect_attempts_, reconnect_delay(), last_good_frame_,
                      "never_received"};
  if (reconnect_started_.has_value()) {
    result.state = StreamState::reconnecting;
    result.reason = "reconnect_backoff";
  } else if (last_good_frame_.has_value() &&
             now - *last_good_frame_ <= config_.freshness_timeout) {
    result.state = StreamState::healthy;
    result.reason = frames_dropped_ == 0 ? "ok" : "queue_full_drop_oldest";
  } else if (last_good_frame_.has_value()) {
    result.reason = "freshness_deadline";
  }
  return result;
}

void CameraStream::mark_reconnect_attempt(Clock::time_point now) {
  ++reconnect_attempts_;
  reconnect_started_ = now;
}

std::chrono::milliseconds CameraStream::reconnect_delay() const {
  if (reconnect_attempts_ == 0) return std::chrono::milliseconds{0};
  auto delay = config_.reconnect_initial;
  for (std::uint64_t attempt = 1; attempt < reconnect_attempts_; ++attempt) {
    if (delay >= config_.reconnect_max / 2) return config_.reconnect_max;
    delay *= 2;
  }
  return std::min(delay, config_.reconnect_max);
}

void CameraStream::mark_reconnected(Clock::time_point now) {
  (void)now;
  reconnect_started_.reset();
}

bool MediaWorker::add_camera(CameraConfig config) {
  const auto [_, inserted] = cameras_.try_emplace(config.camera_id, std::move(config));
  return inserted;
}

bool MediaWorker::ingest(const std::string& camera_id, FrameSample sample, Clock::time_point now) {
  const auto iterator = cameras_.find(camera_id);
  return iterator != cameras_.end() && iterator->second.enqueue(std::move(sample), now);
}

std::optional<FrameSample> MediaWorker::next(const std::string& camera_id) {
  const auto iterator = cameras_.find(camera_id);
  return iterator == cameras_.end() ? std::nullopt : iterator->second.pop();
}

void MediaWorker::mark_reconnect_attempt(const std::string& camera_id, Clock::time_point now) {
  const auto iterator = cameras_.find(camera_id);
  if (iterator != cameras_.end()) iterator->second.mark_reconnect_attempt(now);
}

void MediaWorker::mark_reconnected(const std::string& camera_id, Clock::time_point now) {
  const auto iterator = cameras_.find(camera_id);
  if (iterator != cameras_.end()) iterator->second.mark_reconnected(now);
}

std::vector<CameraHealth> MediaWorker::health(Clock::time_point now) const {
  std::vector<CameraHealth> result;
  result.reserve(cameras_.size());
  for (const auto& [_, camera] : cameras_) result.push_back(camera.health(now));
  return result;
}

std::optional<CandidateEnvelope> MediaWorker::candidate(
    const std::string& camera_id, const FrameSample& sample, Clock::time_point now) const {
  const auto iterator = cameras_.find(camera_id);
  if (iterator == cameras_.end()) return std::nullopt;
  const auto& config = iterator->second.config();
  CandidateEnvelope result;
  const auto identity = camera_id + "/" + sample.track_id + "/" +
                        std::to_string(sample.capture_ts_ns) + "/" +
                        std::to_string(sample.source_pts_ns);
  result.event_id = stable_uuid("event/" + identity);
  result.correlation_id = stable_uuid("correlation/" + camera_id + "/" + sample.track_id);
  result.producer = "tp-smoke-detect.native-reference";
  result.occurred_at = utc_timestamp(sample.capture_ts_ns);
  result.camera_id = camera_id;
  result.track_id = sample.track_id;
  result.camera_config_revision = config.revision;
  result.source_pts_ns = sample.source_pts_ns;
  result.capture_ts_ns = sample.capture_ts_ns;
  result.received_ts_ns = sample.received_ts_ns;
  (void)now;
  result.first_seen_at = utc_timestamp(sample.capture_ts_ns);
  result.last_seen_at = result.first_seen_at;
  result.person_box = sample.person_box;
  result.quality.source_width = sample.width;
  result.quality.source_height = sample.height;
  result.quality.crop_pixels = sample.person_box.width * sample.width *
                               sample.person_box.height * sample.height;
  result.quality.face_pixels = result.quality.crop_pixels * 0.25;
  result.quality.eligible = true;
  result.quality.eligibility_reason = "reference_gate_passed";
  result.observations.independent_channels = {"native_reference"};
  return result;
}

CandidatePublisher::CandidatePublisher(std::size_t max_pending) : max_pending_(max_pending) {
  if (max_pending_ == 0) throw std::invalid_argument("publisher capacity must be positive");
}

bool CandidatePublisher::enqueue(CandidateEnvelope candidate) {
  if (candidate.camera_id.empty() || candidate.track_id.empty()) return false;
  if (pending_.size() >= max_pending_) {
    pending_.pop_front();
    ++dropped_;
  }
  pending_.push_back(std::move(candidate));
  return true;
}

std::size_t CandidatePublisher::flush(
    const std::function<bool(const CandidateEnvelope&)>& send) {
  std::size_t sent = 0;
  while (!pending_.empty()) {
    if (!send(pending_.front())) {
      ++publish_failures_;
      break;
    }
    pending_.pop_front();
    ++sent;
  }
  return sent;
}

const char* to_string(StreamState state) {
  switch (state) {
    case StreamState::healthy: return "healthy";
    case StreamState::degraded: return "degraded";
    case StreamState::reconnecting: return "reconnecting";
  }
  return "unknown";
}

}  // namespace tp_smoke_detect::media
