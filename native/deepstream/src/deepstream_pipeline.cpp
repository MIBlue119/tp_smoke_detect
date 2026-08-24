#include "tp_smoke_detect/media/deepstream_pipeline.hpp"

#include <algorithm>
#include <filesystem>
#include <sstream>
#include <utility>

#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
#include <gst/gst.h>
#include <gstnvdsmeta.h>
#endif

namespace tp_smoke_detect::media {

namespace {

constexpr std::uint64_t kUntrackedObjectId = 0xffffffffffffffffULL;

bool supported_uri(const std::string& uri) {
  return uri.starts_with("rtsp://") || uri.starts_with("rtsps://") ||
         uri.starts_with("file://");
}

#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
std::string join(const std::vector<std::string>& values) {
  std::ostringstream output;
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) output << ", ";
    output << values[index];
  }
  return output.str();
}
#endif

}  // namespace

struct DeepStreamPipeline::RuntimeState {
#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  struct Source {
    DeepStreamSourceConfig config;
    GstElement* element{nullptr};
    GstPad* mux_sink{nullptr};
    bool linked{false};
  };

  GstElement* pipeline{nullptr};
  GstElement* streammux{nullptr};
  GstElement* tracker{nullptr};
  std::vector<Source> sources;
  gulong metadata_probe{0};
  GstBus* bus{nullptr};
#endif
};

DeepStreamPipeline::DeepStreamPipeline(DeepStreamPipelineConfig config)
    : config_(std::move(config)), publisher_(config_.max_pending_candidates) {
  for (const auto& source : config_.sources) {
    worker_.add_camera(CameraConfig{source.camera_id,
                                    source.camera_config_revision.empty()
                                        ? "unresolved"
                                        : source.camera_config_revision});
  }
  readiness_.compiled = TP_SMOKE_DETECT_ENABLE_DEEPSTREAM != 0;
  readiness_.state = readiness_.compiled ? "runtime_unchecked" : "unavailable";
  readiness_.reason = readiness_.compiled ? "runtime_probe_not_run"
                                          : "deepstream_adapter_not_compiled";
}

DeepStreamPipeline::~DeepStreamPipeline() { stop(); }

bool DeepStreamPipeline::add_source(DeepStreamSourceConfig source) {
  if (running_ || source.camera_id.empty() || source.camera_config_revision.empty() ||
      !supported_uri(source.uri) ||
      source.camera_id.size() > 128) {
    return false;
  }
  if (config_.sources.size() >= config_.max_cameras ||
      std::any_of(config_.sources.begin(), config_.sources.end(),
                  [&](const auto& current) {
                    return current.camera_id == source.camera_id ||
                           current.source_id == source.source_id;
                  })) {
    return false;
  }
  if (!worker_.add_camera(CameraConfig{source.camera_id, source.camera_config_revision})) {
    return false;
  }
  config_.sources.push_back(std::move(source));
  return true;
}

void DeepStreamPipeline::set_candidate_callback(CandidateCallback callback) {
  candidate_callback_ = std::move(callback);
}

bool DeepStreamPipeline::validate_config(std::string* error) const {
  const auto fail = [&](const std::string& reason) {
    if (error != nullptr) *error = reason;
    return false;
  };
  if (config_.max_cameras == 0 || config_.batch_size == 0 ||
      config_.max_pending_candidates == 0) {
    return fail("invalid DeepStream bounds: cameras, batch, and pending candidates must be positive");
  }
  if (config_.sources.empty()) return fail("at least one RTSP or file source is required");
  if (config_.sources.size() > config_.max_cameras ||
      config_.sources.size() > config_.batch_size) {
    return fail("source count exceeds the configured camera or batch bound");
  }
  for (const auto& source : config_.sources) {
    if (source.camera_id.empty() || !supported_uri(source.uri)) {
      return fail("source URI must use rtsp://, rtsps://, or file:// and camera_id is required");
    }
    const auto duplicate = std::count_if(
        config_.sources.begin(), config_.sources.end(), [&](const auto& candidate) {
          return candidate.source_id == source.source_id;
        });
    if (duplicate != 1) return fail("source IDs must be unique within nvstreammux batch");
  }
  if (config_.detector_config.empty() || config_.tracker_config.empty() ||
      config_.pose_model_path.empty() || config_.hand_model_path.empty() ||
      config_.crop_infer_config.empty()) {
    return fail("detector, tracker, pose, hand, and crop inference configuration are required");
  }
  return true;
}

void DeepStreamPipeline::set_failure(std::string reason) {
  readiness_.graph_constructed = false;
  readiness_.qualified = false;
  readiness_.state = "unqualified";
  readiness_.reason = std::move(reason);
}

std::string DeepStreamPipeline::graph_description() const {
  return "nvurisrcbin(rtsp/file)->nvstreammux->nvinfer(PeopleNet Transformer)"
         "->nvtracker(NvDCF)->tpmediapipepose(GPU)->tpmediapipehands(GPU)"
         "->nvinferserver(SigLIP2 CUDA shared-memory boundary)->typed candidate";
}

std::vector<CameraHealth> DeepStreamPipeline::camera_health(Clock::time_point now) const {
  return worker_.health(now);
}

bool DeepStreamPipeline::available() const {
#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  if (!readiness_.compiled) return false;
  int argc = 0;
  gst_init_check(&argc, nullptr, nullptr);
  constexpr const char* required[] = {"nvurisrcbin", "nvstreammux", "nvinfer", "nvtracker",
                                      "nvinferserver", "tpmediapipepose", "tpmediapipehands"};
  for (const char* name : required) {
    GstElementFactory* factory = gst_element_factory_find(name);
    if (factory == nullptr) return false;
    gst_object_unref(factory);
  }
  return true;
#else
  return false;
#endif
}

#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
namespace {

void set_string_property(GObject* object, const char* property, const std::string& value) {
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(object), property) != nullptr) {
    g_object_set(object, property, value.c_str(), nullptr);
  }
}

void set_uint_property(GObject* object, const char* property, std::uint32_t value) {
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(object), property) != nullptr) {
    g_object_set(object, property, value, nullptr);
  }
}

void set_bool_property(GObject* object, const char* property, bool value) {
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(object), property) != nullptr) {
    g_object_set(object, property, value, nullptr);
  }
}

GstPadProbeReturn metadata_probe(GstPad*, GstPadProbeInfo* info, gpointer user_data) {
  auto* owner = static_cast<DeepStreamPipeline*>(user_data);
  if (info == nullptr || info->buffer == nullptr) return GST_PAD_PROBE_OK;
  owner->on_metadata_buffer(info->buffer);
  return GST_PAD_PROBE_OK;
}

void source_pad_added(GstElement* element, GstPad* pad, gpointer user_data) {
  auto* owner = static_cast<DeepStreamPipeline*>(user_data);
  owner->on_source_pad(element, pad);
}

GstBusSyncReply bus_sync(GstBus*, GstMessage* message, gpointer user_data) {
  auto* owner = static_cast<DeepStreamPipeline*>(user_data);
  owner->on_bus_message(message);
  return GST_BUS_PASS;
}

}  // namespace
#endif

#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
void DeepStreamPipeline::on_metadata_buffer(void* opaque_buffer) {
  auto* runtime = runtime_.get();
  auto* buffer = static_cast<GstBuffer*>(opaque_buffer);
  if (runtime == nullptr || buffer == nullptr) return;
  NvDsBatchMeta* batch = gst_buffer_get_nvds_batch_meta(buffer);
  if (batch == nullptr) return;

  for (NvDsMetaList* frame_node = batch->frame_meta_list; frame_node != nullptr;
       frame_node = frame_node->next) {
    auto* frame = static_cast<NvDsFrameMeta*>(frame_node->data);
    if (frame == nullptr) continue;
    auto source = std::find_if(runtime->sources.begin(), runtime->sources.end(),
                               [&](const auto& item) {
                                 return item.config.source_id == frame->source_id;
                               });
    if (source == runtime->sources.end()) continue;
    if (frame->buf_pts == GST_CLOCK_TIME_NONE) continue;
    const auto width = frame->source_frame_width > 0 ? frame->source_frame_width : 0;
    const auto height = frame->source_frame_height > 0 ? frame->source_frame_height : 0;
    for (NvDsMetaList* object_node = frame->obj_meta_list; object_node != nullptr;
         object_node = object_node->next) {
      auto* object = static_cast<NvDsObjectMeta*>(object_node->data);
      if (object == nullptr || object->class_id != 0 || object->object_id == kUntrackedObjectId) {
        continue;
      }
      FrameSample sample;
      sample.source_pts_ns = frame->buf_pts;
      sample.capture_ts_ns = frame->buf_pts;
      sample.received_ts_ns = frame->buf_pts;
      sample.width = width;
      sample.height = height;
      sample.track_id = "track-" + std::to_string(object->object_id);
      sample.person_box = BoundingBox{
          std::clamp(static_cast<double>(object->rect_params.left) / width, 0.0, 1.0),
          std::clamp(static_cast<double>(object->rect_params.top) / height, 0.0, 1.0),
          std::clamp(static_cast<double>(object->rect_params.width) / width, 0.000001, 1.0),
          std::clamp(static_cast<double>(object->rect_params.height) / height, 0.000001, 1.0),
          std::clamp(static_cast<double>(object->confidence), 0.0, 1.0)};
      if (!worker_.ingest(source->config.camera_id, sample, Clock::now())) continue;
      auto candidate = worker_.candidate(source->config.camera_id, sample, Clock::now());
      if (!candidate.has_value()) continue;
      candidate->producer = "tp-smoke-detect.deepstream7";
      candidate->stage = "candidate";
      candidate->quality.eligible = width > 0 && height > 0 && object->confidence > 0.0;
      candidate->quality.eligibility_reason = candidate->quality.eligible
                                                  ? "gpu_quality_gate_pending_role_receipts"
                                                  : "missing_source_dimensions_or_confidence";
      if (!candidate->quality.eligible) continue;
      candidate->inference_receipts = {
          InferenceReceipt{.role = "detector",
                           .status = "ok",
                           .reason_code = "none",
                           .request_id = candidate->event_id,
                           .correlation_id = candidate->correlation_id,
                           .model_revision = "peoplenet-transformer:unresolved",
                           .artifact_revision = "bundle:unresolved",
                           .output_schema = "detector.v1",
                           .deadline_outcome = "met"},
      };
      candidate->model_revisions = {{"detector", "peoplenet-transformer:unresolved"}};
      publisher_.enqueue(*candidate);
      if (candidate_callback_) publisher_.flush(candidate_callback_);
    }
  }
}

void DeepStreamPipeline::on_source_pad(void* opaque_element, void* opaque_pad) {
  auto* runtime = runtime_.get();
  auto* element = static_cast<GstElement*>(opaque_element);
  auto* pad = static_cast<GstPad*>(opaque_pad);
  if (runtime == nullptr || element == nullptr || pad == nullptr) return;
  auto source = std::find_if(runtime->sources.begin(), runtime->sources.end(),
                             [&](const auto& item) { return item.element == element; });
  if (source == runtime->sources.end() || source->mux_sink == nullptr || source->linked) return;
  GstCaps* caps = gst_pad_get_current_caps(pad);
  if (caps == nullptr) caps = gst_pad_query_caps(pad, nullptr);
  bool video = false;
  if (caps != nullptr && gst_caps_get_size(caps) > 0) {
    const GstStructure* structure = gst_caps_get_structure(caps, 0);
    const char* name = gst_structure_get_name(structure);
    video = name != nullptr && g_str_has_prefix(name, "video/");
  }
  if (caps != nullptr) gst_caps_unref(caps);
  if (video) source->linked = gst_pad_link(pad, source->mux_sink) == GST_PAD_LINK_OK;
}

void DeepStreamPipeline::on_bus_message(void* opaque_message) {
  auto* runtime = runtime_.get();
  auto* message = static_cast<GstMessage*>(opaque_message);
  if (runtime == nullptr || message == nullptr) return;
  if (GST_MESSAGE_TYPE(message) != GST_MESSAGE_ERROR &&
      GST_MESSAGE_TYPE(message) != GST_MESSAGE_EOS) {
    return;
  }
  auto source = std::find_if(runtime->sources.begin(), runtime->sources.end(),
                             [&](const auto& item) {
                               return GST_MESSAGE_SRC(message) == GST_OBJECT(item.element);
                             });
  if (source != runtime->sources.end()) {
    worker_.mark_reconnect_attempt(source->config.camera_id, Clock::now());
  }
}

#endif

bool DeepStreamPipeline::start(std::string* error) {
  if (running_) return true;
#if !TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  set_failure("unqualified: DeepStream SDK is unavailable; build with "
              "-DTP_SMOKE_DETECT_ENABLE_DEEPSTREAM=ON and a verified DS7 SDK");
  if (error != nullptr) *error = readiness_.reason;
  return false;
#else
  int argc = 0;
  GError* init_error = nullptr;
  if (!gst_init_check(&argc, nullptr, &init_error)) {
    set_failure("unqualified: GStreamer initialization failed");
    if (error != nullptr) *error = readiness_.reason;
    if (init_error != nullptr) g_error_free(init_error);
    return false;
  }
  readiness_.runtime_present = true;
  constexpr const char* required[] = {"nvurisrcbin", "nvstreammux", "nvinfer", "nvtracker",
                                      "nvinferserver", "tpmediapipepose", "tpmediapipehands"};
  std::vector<std::string> missing;
  for (const char* name : required) {
    GstElementFactory* factory = gst_element_factory_find(name);
    if (factory == nullptr) missing.emplace_back(name);
    else gst_object_unref(factory);
  }
  if (!missing.empty()) {
    set_failure("unqualified: missing DeepStream/MediaPipe runtime element(s): " + join(missing) +
                "; qualification receipt remains unqualified");
    if (error != nullptr) *error = readiness_.reason;
    return false;
  }
  if (!validate_config(error)) {
    set_failure("unqualified: " + (error == nullptr ? std::string{"invalid configuration"} : *error));
    return false;
  }
  const std::vector<std::pair<const char*, const std::string*>> files = {
      {"detector config", &config_.detector_config},
      {"tracker config", &config_.tracker_config},
      {"pose model", &config_.pose_model_path},
      {"hand model", &config_.hand_model_path},
      {"crop inference config", &config_.crop_infer_config},
  };
  for (const auto& [label, path] : files) {
    if (!std::filesystem::is_regular_file(*path)) {
      set_failure("unqualified: missing " + std::string(label) +
                  " artifact; qualification receipt remains unqualified");
      if (error != nullptr) *error = readiness_.reason;
      return false;
    }
  }

  runtime_ = std::make_unique<RuntimeState>();
  runtime_->pipeline = gst_pipeline_new("tp-smoke-detect-deepstream7");
  runtime_->streammux = gst_element_factory_make("nvstreammux", "stream-mux");
  GstElement* detector = gst_element_factory_make("nvinfer", "person-detector");
  runtime_->tracker = gst_element_factory_make("nvtracker", "nvdcf-tracker");
  GstElement* pose = gst_element_factory_make("tpmediapipepose", "mediapipe-pose-gpu");
  GstElement* hands = gst_element_factory_make("tpmediapipehands", "mediapipe-hands-gpu");
  GstElement* crop = gst_element_factory_make("nvinferserver", "siglip2-crop");
  if (runtime_->pipeline == nullptr || runtime_->streammux == nullptr || detector == nullptr ||
      runtime_->tracker == nullptr || pose == nullptr || hands == nullptr || crop == nullptr) {
    set_failure("unqualified: DeepStream graph element construction failed");
    if (error != nullptr) *error = readiness_.reason;
    stop();
    return false;
  }
  set_uint_property(G_OBJECT(runtime_->streammux), "batch-size",
                    static_cast<std::uint32_t>(config_.batch_size));
  set_uint_property(G_OBJECT(runtime_->streammux), "batched-push-timeout",
                    static_cast<std::uint32_t>(config_.batched_push_timeout_us));
  set_bool_property(G_OBJECT(runtime_->streammux), "live-source", config_.live_source);
  set_string_property(G_OBJECT(detector), "config-file-path", config_.detector_config);
  set_string_property(G_OBJECT(runtime_->tracker), "ll-config-file", config_.tracker_config);
  set_string_property(G_OBJECT(pose), "model-file", config_.pose_model_path);
  set_string_property(G_OBJECT(hands), "model-file", config_.hand_model_path);
  set_bool_property(G_OBJECT(pose), "use-gpu", true);
  set_bool_property(G_OBJECT(hands), "use-gpu", true);
  set_string_property(G_OBJECT(crop), "config-file-path", config_.crop_infer_config);
  gst_bin_add_many(GST_BIN(runtime_->pipeline), runtime_->streammux, detector, runtime_->tracker,
                   pose, hands, crop, nullptr);
  if (!gst_element_link_many(runtime_->streammux, detector, runtime_->tracker, pose, hands, crop,
                             nullptr)) {
    set_failure("unqualified: DeepStream graph link failed; candidate publication is disabled");
    if (error != nullptr) *error = readiness_.reason;
    stop();
    return false;
  }
  runtime_->sources.reserve(config_.sources.size());
  for (const auto& source_config : config_.sources) {
    RuntimeState::Source source;
    source.config = source_config;
    const std::string element_name = "source-" + std::to_string(source_config.source_id);
    source.element = gst_element_factory_make("nvurisrcbin", element_name.c_str());
    if (source.element == nullptr) {
      set_failure("unqualified: nvurisrcbin construction failed for a configured camera");
      if (error != nullptr) *error = readiness_.reason;
      stop();
      return false;
    }
    set_string_property(G_OBJECT(source.element), "uri", source.config.uri);
    set_uint_property(G_OBJECT(source.element), "source-id", source.config.source_id);
    set_uint_property(G_OBJECT(source.element), "latency", source.config.latency_ms);
    set_bool_property(G_OBJECT(source.element), "drop-on-latency", true);
    set_uint_property(G_OBJECT(source.element), "rtsp-reconnect-interval",
                      source.config.reconnect_interval_s);
    set_uint_property(G_OBJECT(source.element), "rtsp-reconnect-attempts",
                      source.config.reconnect_attempts);
    gst_bin_add(GST_BIN(runtime_->pipeline), source.element);
    const std::string pad_name = "sink_" + std::to_string(source.config.source_id);
    source.mux_sink = gst_element_request_pad_simple(runtime_->streammux, pad_name.c_str());
    if (source.mux_sink == nullptr) {
      set_failure("unqualified: nvstreammux request pad allocation failed");
      if (error != nullptr) *error = readiness_.reason;
      stop();
      return false;
    }
    runtime_->sources.push_back(source);
  }
  for (auto& source : runtime_->sources) {
    g_signal_connect(source.element, "pad-added", G_CALLBACK(source_pad_added), this);
  }
  GstPad* tracker_src = gst_element_get_static_pad(runtime_->tracker, "src");
  if (tracker_src == nullptr) {
    set_failure("unqualified: tracker metadata pad is unavailable");
    if (error != nullptr) *error = readiness_.reason;
    stop();
    return false;
  }
  runtime_->metadata_probe = gst_pad_add_probe(tracker_src, GST_PAD_PROBE_TYPE_BUFFER,
                                                metadata_probe, this, nullptr);
  gst_object_unref(tracker_src);
  runtime_->bus = gst_element_get_bus(runtime_->pipeline);
  gst_bus_set_sync_handler(runtime_->bus, bus_sync, this, nullptr);
  const GstStateChangeReturn state = gst_element_set_state(runtime_->pipeline, GST_STATE_PLAYING);
  if (state == GST_STATE_CHANGE_FAILURE) {
    set_failure("unqualified: DeepStream graph failed to enter PLAYING; qualification receipt remains unqualified");
    if (error != nullptr) *error = readiness_.reason;
    stop();
    return false;
  }
  running_ = true;
  readiness_.graph_constructed = true;
  readiness_.qualified = false;
  readiness_.state = "running-unqualified";
  readiness_.reason = "real graph constructed; one-stream and capacity qualification receipt required";
  return true;
#endif
}

void DeepStreamPipeline::stop() {
#if TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
  if (runtime_ != nullptr) {
    if (runtime_->pipeline != nullptr) gst_element_set_state(runtime_->pipeline, GST_STATE_NULL);
    for (auto& source : runtime_->sources) {
      if (source.mux_sink != nullptr && runtime_->streammux != nullptr) {
        gst_element_release_request_pad(runtime_->streammux, source.mux_sink);
        gst_object_unref(source.mux_sink);
        source.mux_sink = nullptr;
      }
    }
    if (runtime_->bus != nullptr) gst_object_unref(runtime_->bus);
    if (runtime_->pipeline != nullptr) gst_object_unref(runtime_->pipeline);
  }
#endif
  runtime_.reset();
  running_ = false;
  if (readiness_.compiled) {
    readiness_.graph_constructed = false;
    readiness_.qualified = false;
    readiness_.state = "stopped-unqualified";
    readiness_.reason = "stopped; no qualification receipt was produced";
  }
}

}  // namespace tp_smoke_detect::media
