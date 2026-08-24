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

void inference_receipt(std::ostringstream& output, const InferenceReceipt& receipt) {
  output << '{';
  key(output, "role"); quote(output, receipt.role);
  output << ','; key(output, "status"); quote(output, receipt.status);
  output << ','; key(output, "reason_code"); quote(output, receipt.reason_code);
  output << ','; key(output, "request_id");
  if (receipt.request_id.empty()) output << "null"; else quote(output, receipt.request_id);
  output << ','; key(output, "correlation_id");
  if (receipt.correlation_id.empty()) output << "null";
  else quote(output, receipt.correlation_id);
  output << ','; key(output, "model_revision"); quote(output, receipt.model_revision);
  output << ','; key(output, "artifact_revision"); quote(output, receipt.artifact_revision);
  output << ','; key(output, "output_schema"); quote(output, receipt.output_schema);
  output << ','; key(output, "deadline_outcome"); quote(output, receipt.deadline_outcome);
  output << ','; key(output, "score");
  if (receipt.has_score) output << receipt.score; else output << "null";
  output << ','; key(output, "reviewer");
  if (!receipt.has_reviewer) {
    output << "null";
  } else {
    output << '{';
    key(output, "label"); quote(output, receipt.reviewer.label);
    output << ','; key(output, "score"); output << receipt.reviewer.score;
    output << ','; key(output, "reason_code"); quote(output, receipt.reviewer.reason_code);
    output << '}';
  }
  output << '}';
}

void inference_receipt_array(std::ostringstream& output,
                             const std::vector<InferenceReceipt>& receipts) {
  output << '[';
  for (std::size_t index = 0; index < receipts.size(); ++index) {
    if (index != 0) output << ',';
    inference_receipt(output, receipts[index]);
  }
  output << ']';
}

void model_revision_map(std::ostringstream& output,
                        const std::vector<std::pair<std::string, std::string>>& revisions) {
  output << '{';
  for (std::size_t index = 0; index < revisions.size(); ++index) {
    if (index != 0) output << ',';
    key(output, revisions[index].first.c_str());
    quote(output, revisions[index].second);
  }
  output << '}';
}

}  // namespace

std::string CandidateEnvelope::to_json() const {
  std::ostringstream output;
  output << std::setprecision(17);
  output << '{';
  key(output, "schema_version"); quote(output, "track.candidate.v1");
  output << ','; key(output, "event_id"); quote(output, event_id);
  output << ','; key(output, "correlation_id"); quote(output, correlation_id);
  output << ','; key(output, "producer"); quote(output, producer);
  output << ','; key(output, "occurred_at"); quote(output, occurred_at);
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
  key(output, "pose");
  if (!observations.has_pose) output << "null";
  else {
    output << '{';
    key(output, "hand_to_mouth_distance"); output << observations.hand_to_mouth_distance;
    output << ','; key(output, "mouth_dwell_ms"); output << observations.mouth_dwell_ms;
    output << ','; key(output, "confidence"); output << observations.pose_confidence << '}';
  }
  output << ','; key(output, "objects");
  if (!observations.has_object) output << "[]";
  else {
    output << "[{";
    key(output, "label"); quote(output, observations.object_label);
    output << ','; key(output, "confidence"); output << observations.object_confidence << "}]";
  }
  output << ','; key(output, "smoke");
  if (!observations.has_smoke) output << "null";
  else {
    output << '{';
    key(output, "smoke_score"); output << observations.smoke_score;
    output << ','; key(output, "ember_score"); output << observations.ember_score << '}';
  }
  output << ','; key(output, "temporal");
  if (!observations.has_temporal) output << "null";
  else {
    output << '{';
    key(output, "hand_retreat"); output << (observations.hand_retreat ? "true" : "false");
    output << ','; key(output, "cycle_interval_ms"); output << observations.cycle_interval_ms;
    output << ','; key(output, "persistence_ms"); output << observations.persistence_ms << '}';
  }
  output << ','; key(output, "independent_channels"); string_array(output, observations.independent_channels);
  output << '}';
  output << ','; key(output, "inference_receipts"); inference_receipt_array(output, inference_receipts);
  output << ','; key(output, "model_revisions"); model_revision_map(output, model_revisions);
  output << '}';
  return output.str();
}

}  // namespace tp_smoke_detect::media
