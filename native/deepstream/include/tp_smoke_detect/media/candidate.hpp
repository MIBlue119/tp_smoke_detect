#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tp_smoke_detect::media {

struct BoundingBox {
  double x{0.0};
  double y{0.0};
  double width{0.0};
  double height{0.0};
  double confidence{0.0};
};

struct CandidateQuality {
  std::int32_t source_width{0};
  std::int32_t source_height{0};
  double face_pixels{0.0};
  double crop_pixels{0.0};
  std::string illumination_profile{"unknown"};
  bool eligible{false};
  std::string eligibility_reason{"not_evaluated"};
};

struct CandidateObservations {
  double hand_to_mouth_distance{0.0};
  std::int32_t mouth_dwell_ms{0};
  double pose_confidence{0.0};
  std::string object_label{"unknown"};
  double object_confidence{0.0};
  double smoke_score{0.0};
  double ember_score{0.0};
  bool hand_retreat{false};
  std::int32_t cycle_interval_ms{0};
  std::int32_t persistence_ms{0};
  std::vector<std::string> independent_channels;
};

struct CandidateEnvelope {
  // Delivery identity is mandatory for every newly emitted broker envelope.
  // Values use UUID text so the native producer can be validated by the same
  // versioned Python contract as replay and API producers.
  std::string event_id;
  std::string correlation_id;
  std::string producer{"tp-smoke-detect.native-reference"};
  std::string occurred_at;
  std::string camera_id;
  std::string track_id;
  std::string camera_config_revision;
  std::uint64_t source_pts_ns{0};
  std::uint64_t capture_ts_ns{0};
  std::uint64_t received_ts_ns{0};
  std::string first_seen_at;
  std::string last_seen_at;
  std::string stage{"detected"};
  std::vector<std::string> artifact_ids;
  BoundingBox person_box;
  std::string roi_id;
  double coverage_score{1.0};
  CandidateQuality quality;
  CandidateObservations observations;

  // Canonical JSON for track.candidate.v1. No image bytes or free-form model
  // output are representable by this type.
  [[nodiscard]] std::string to_json() const;
};

}  // namespace tp_smoke_detect::media
