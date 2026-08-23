#include "tp_smoke_detect/media/candidate.hpp"

#include <iomanip>
#include <sstream>

namespace tp_smoke_detect::media {
namespace {

void quote(std::ostringstream& output, const std::string& value) {
  output << '"';
  for (const char character : value) {
    switch (character) {
      case '"': output << "\\\""; break;
      case '\\': output << "\\\\"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default: output << character; break;
    }
  }
  output << '"';
}

void key(std::ostringstream& output, const char* name) {
  quote(output, name);
  output << ':';
}

void string_array(std::ostringstream& output, const std::vector<std::string>& values) {
  output << '[';
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) output << ',';
    quote(output, values[index]);
  }
  output << ']';
}

}  // namespace

std::string CandidateEnvelope::to_json() const {
  std::ostringstream output;
  output << std::setprecision(17);
  output << '{';
  key(output, "schema_version"); quote(output, "track.candidate.v1");
  output << ','; key(output, "camera_id"); quote(output, camera_id);
  output << ','; key(output, "track_id"); quote(output, track_id);
  output << ','; key(output, "camera_config_revision"); quote(output, camera_config_revision);
  output << ','; key(output, "source_pts_ns"); output << source_pts_ns;
  output << ','; key(output, "capture_ts_ns"); output << capture_ts_ns;
  output << ','; key(output, "received_ts_ns"); output << received_ts_ns;
  output << ','; key(output, "first_seen_at"); quote(output, first_seen_at);
  output << ','; key(output, "last_seen_at"); quote(output, last_seen_at);
  output << ','; key(output, "stage"); quote(output, stage);
  output << ','; key(output, "artifact_ids"); string_array(output, artifact_ids);
  output << ','; key(output, "geometry"); output << '{';
  key(output, "person_box"); output << '{';
  key(output, "x"); output << person_box.x;
  output << ','; key(output, "y"); output << person_box.y;
  output << ','; key(output, "width"); output << person_box.width;
  output << ','; key(output, "height"); output << person_box.height;
  output << ','; key(output, "confidence"); output << person_box.confidence;
  output << '}';
  output << ','; key(output, "roi_id");
  if (roi_id.empty()) output << "null"; else quote(output, roi_id);
  output << ','; key(output, "coverage_score"); output << coverage_score << '}';
  output << ','; key(output, "quality"); output << '{';
  key(output, "source_width"); output << quality.source_width;
  output << ','; key(output, "source_height"); output << quality.source_height;
  output << ','; key(output, "face_pixels"); output << quality.face_pixels;
  output << ','; key(output, "crop_pixels"); output << quality.crop_pixels;
  output << ','; key(output, "illumination_profile"); quote(output, quality.illumination_profile);
  output << ','; key(output, "eligible"); output << (quality.eligible ? "true" : "false");
  output << ','; key(output, "eligibility_reason"); quote(output, quality.eligibility_reason); output << '}';
  output << ','; key(output, "observations"); output << '{';
  key(output, "pose"); output << '{';
  key(output, "hand_to_mouth_distance"); output << observations.hand_to_mouth_distance;
  output << ','; key(output, "mouth_dwell_ms"); output << observations.mouth_dwell_ms;
  output << ','; key(output, "confidence"); output << observations.pose_confidence << '}';
  output << ','; key(output, "objects"); output << "[{";
  key(output, "label"); quote(output, observations.object_label);
  output << ','; key(output, "confidence"); output << observations.object_confidence << "}]";
  output << ','; key(output, "smoke"); output << '{';
  key(output, "smoke_score"); output << observations.smoke_score;
  output << ','; key(output, "ember_score"); output << observations.ember_score << '}';
  output << ','; key(output, "temporal"); output << '{';
  key(output, "hand_retreat"); output << (observations.hand_retreat ? "true" : "false");
  output << ','; key(output, "cycle_interval_ms"); output << observations.cycle_interval_ms;
  output << ','; key(output, "persistence_ms"); output << observations.persistence_ms << '}';
  output << ','; key(output, "independent_channels"); string_array(output, observations.independent_channels);
  output << "}" << '}';
  return output.str();
}

}  // namespace tp_smoke_detect::media
