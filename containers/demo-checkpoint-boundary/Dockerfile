FROM python:3.11-slim-bookworm@sha256:2e32f7d302adc1c37428355c1e646897c0c53f4fd60b6a551245fb90ee129f91

# Disposable checkpoint conversion boundary; it never receives camera media.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir "torch==2.4.1" "ultralytics==8.3.0" "safetensors==0.4.5" "onnx==1.16.1"
RUN groupadd --gid 65532 boundary && useradd --uid 65532 --gid 65532 --create-home --shell /usr/sbin/nologin boundary
COPY checkpoint_boundary.py /opt/checkpoint_boundary.py
USER 65532:65532
WORKDIR /outputs
ENTRYPOINT ["python", "/opt/checkpoint_boundary.py"]
