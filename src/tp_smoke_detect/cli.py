"""Small, dependency-light command line entry points used by agents and CI."""

from __future__ import annotations

import argparse
import importlib
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters.artifacts.local import LocalArtifactStore
from .adapters.audio.fake import FakeAudioController
from .adapters.messaging.in_memory import InMemoryMessageBus
from .adapters.messaging.mqtt import MqttCandidateConsumer, MqttConsumerConfig
from .adapters.persistence.sqlite import SQLiteAuditRepository
from .application.candidate_processing import build_candidate_processing_service
from .application.replay import ReplayWorker, synthetic_camera, synthetic_manifest
from .observability.health import HealthRegistry
from .observability.metrics import OperationalMetrics
from .schema import write_schemas
from .settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smoke-detect")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("schema", help="write canonical v1 JSON schemas")
    schema.add_argument("--output", type=Path, default=Path("schemas/smoke/v1"))

    validate = subparsers.add_parser("validate-config", help="validate an example YAML file")
    validate.add_argument("path", type=Path)

    demo = subparsers.add_parser("demo", help="CPU reference path placeholder")
    demo.add_argument("--fixture", choices=["synthetic"], default="synthetic")

    serve = subparsers.add_parser(
        "serve-candidates", help="start the bounded GPU candidate MQTT consumer"
    )
    serve.add_argument("--config", type=Path, default=None)
    serve.add_argument("--workers", type=int, default=None)
    serve.add_argument(
        "--ready-file",
        type=Path,
        default=None,
        help="write this file only after the broker consumer has connected",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schema":
        for path in write_schemas(args.output):
            print(path)
        return 0
    if args.command == "validate-config":
        settings = load_settings(args.path)
        print(f"validated {settings.service_name} ({len(settings.cameras)} cameras)")
        return 0
    if args.command == "demo":
        if args.fixture == "synthetic":
            with TemporaryDirectory(prefix="smoke-detect-replay-") as directory:
                root = Path(directory)
                fixture = root / "synthetic"
                fixture.mkdir()
                # Minimal valid PNG signatures are sufficient for the replay
                # adapter; pixel decoding belongs to the media worker.
                png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)
                for name in ("frame-000.png", "frame-005.png"):
                    (fixture / name).write_bytes(png)
                store = LocalArtifactStore(root)
                bus = InMemoryMessageBus()
                result = ReplayWorker(synthetic_camera(), store, bus).replay(synthetic_manifest())
                print(
                    f"CPU fixture ready: {args.fixture} "
                    f"candidates={len(result.candidates)} errors={len(result.errors)}"
                )
                return 0 if result.ok else 1
        print(f"CPU fixture ready: {args.fixture}")
        return 0
    if args.command == "serve-candidates":
        settings = load_settings(args.config)
        repository = SQLiteAuditRepository(settings.database)
        metrics = OperationalMetrics()
        health = HealthRegistry(metrics)
        service = build_candidate_processing_service(
            repository,
            settings=settings,
            audio_controller=FakeAudioController(),
            metrics=metrics,
            health=health,
        )
        try:
            mqtt = importlib.import_module("paho.mqtt.client")
        except ImportError as exc:
            raise SystemExit(
                "serve-candidates requires the optional GPU MQTT dependency: paho-mqtt"
            ) from exc
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, manual_ack=True)
        consumer = MqttCandidateConsumer(
            service,
            config=MqttConsumerConfig(
                topic=settings.candidate.topic,
                dead_letter_topic=settings.candidate.dead_letter_topic,
                max_inflight=settings.candidate.max_inflight,
                retry_limit=settings.candidate.retry_limit,
            ),
            client=client,
            metrics=metrics,
            health=health,
        )
        consumer.start(workers=args.workers)
        client.connect(
            settings.candidate.broker_host,
            settings.candidate.broker_port,
            settings.candidate.broker_keepalive_seconds,
        )
        if args.ready_file is not None:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text("ready\n", encoding="utf-8")
        try:
            while consumer.ready:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if args.ready_file is not None:
                args.ready_file.unlink(missing_ok=True)
            consumer.stop()
            repository.close()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
