# VALIS registration image for nf-austin/valis.
#
# Built in two variants from this one file, selected by BASE_IMAGE/TORCH_VARIANT:
#
#   CPU (default) : python:3.11-slim                              linux/amd64, linux/arm64
#   GPU           : pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime linux/amd64
#
# The CPU variant is multi-arch so it runs natively on Apple Silicon for local
# testing; a CUDA base would be amd64-only and force QEMU emulation there.
ARG BASE_IMAGE=python:3.11-slim

# ---------------------------------------------------------------------------
# Builder: compiles the dependencies that ship no wheel for every platform.
# aicspylibczi in particular has no linux/arm64 wheel and needs cmake + a C++
# toolchain, none of which belongs in the runtime image.
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS builder

ARG VALIS_VERSION=1.2.0
# cpu -> install CPU-only torch before valis-wsi. torch is a hard VALIS
# dependency and the default PyPI wheel bundles the CUDA runtime on BOTH amd64
# and aarch64 (~4 GB of nvidia-* packages), all dead weight in the CPU image.
# cuda -> skip, because the CUDA base image already ships torch.
ARG TORCH_VARIANT=cpu

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# --system-site-packages so the CUDA base's preinstalled torch stays visible
# instead of being shadowed and reinstalled.
RUN python -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN if [ "${TORCH_VARIANT}" = "cpu" ]; then \
      pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision ; \
    fi

RUN pip install "valis-wsi==${VALIS_VERSION}"

# Guard the intent above: the CPU image must not contain the CUDA runtime.
RUN if [ "${TORCH_VARIANT}" = "cpu" ]; then \
      python -c "import torch, sys; sys.exit(0 if torch.version.cuda is None else 1)" \
      || { echo 'ERROR: CPU image picked up a CUDA torch build'; exit 1; } ; \
    fi

# Apply MathOnco/valis PR #239 ("Fix transfer of data from GPU to CPU after
# computation"): tensors are handed to numpy without .cpu(), which raises on
# CUDA (upstream issue #208, still open). Mechanical and safe on CPU too.
#
# find -exec rather than $PKG/*.py because the glob would miss subpackages such
# as valis/superglue_models/. The grep turns a missed site into a build failure
# instead of a runtime crash on the GPU path -- and if it ever fails because
# there is nothing left to patch, upstream has merged the fix and this whole
# step can go.
RUN PKG="$(python -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("valis").origin))')" \
 && find "$PKG" -name '*.py' -exec sed -i 's/\.detach()\.numpy()/.detach().cpu().numpy()/g' {} + \
 && ! grep -rn '\.detach()\.numpy()' "$PKG" \
 && echo "PR #239 patch applied cleanly"

# Second upstream patch: LightGlueMatcher.match_images feeds keypoints and
# descriptors to kornia's LightGlue via torch.from_numpy() with no dtype cast,
# while the model weights are float32. SuperGlueMatcher a few lines above does
# cast (.astype(np.float32)); LightGlue was missed.
#
# It only bites when the incoming arrays are float64, which is exactly what the
# reflection search produces -- it derives flipped keypoints with numpy, whose
# default is double. Result: registration with check_for_reflections=True dies
# with "expected m1 and m2 to have the same dtype, but got: double != float",
# and because register() swallows exceptions it surfaces only as a None return.
#
# LightGlue is float32 throughout, so casting is the semantically correct fix.
RUN PKG="$(python -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("valis").origin))')" \
 && sed -i -E 's/torch\.from_numpy\((kp1_xy|kp2_xy|desc1|desc2|r_kp2|r_desc2)\)\.to\(self\.device\)/torch.from_numpy(\1).float().to(self.device)/g' \
      "${PKG}/feature_matcher.py" \
 && sed -i -E 's/^(\s*)t_desc1 = torch\.from_numpy\(desc1\)$/\1t_desc1 = torch.from_numpy(desc1).float()/' \
      "${PKG}/feature_matcher.py" \
 && ! grep -nE 'torch\.from_numpy\((kp1_xy|kp2_xy|desc1|desc2|r_kp2|r_desc2)\)(\.to\(self\.device\))?$' \
      "${PKG}/feature_matcher.py" \
 && echo "LightGlue dtype patch applied cleanly"

# Bio-Formats jar. VALIS looks for it at <package dir>/bioformats_package.jar
# (slide_io.init_jvm) and only falls back to a Maven download -- which needs
# network egress at runtime -- when it is absent.
ARG BF_VERSION=7.0.0
RUN PKG="$(python -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("valis").origin))')" \
 && curl -fsSL -o "${PKG}/bioformats_package.jar" \
      "https://downloads.openmicroscopy.org/bio-formats/${BF_VERSION}/artifacts/bioformats_package.jar"

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

# TORCH_HOME is pinned to a fixed, world-readable path: without it the baked-in
# weights land in $HOME, which Nextflow may remap at runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:${PATH}" \
    TORCH_HOME=/opt/torch

# Runtime native stack VALIS needs:
#   default-jre-headless -> Bio-Formats via jpype (openjdk-11-jre is not in
#                           Debian trixie; the metapackage resolves per distro)
#   libvips              -> pyvips (VALIS needs >= 8.11)
#   libopenslide         -> OpenSlide-only formats (.mrxs, .scn, ...)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      default-jre-headless \
      libvips \
      libopenslide0 \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# init_jvm() writes <package dir>/data/bf_formats.txt on every start, so that
# directory has to stay writable when the container runs as a non-root uid,
# which is what Nextflow's docker profile does.
RUN PKG="$(python -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("valis").origin))')" \
 && mkdir -p "${PKG}/data" \
 && chmod -R a+rwX "${PKG}/data"

# Pre-download model weights so runs need no network egress. Upstream bakes
# DISK/DeDoDe/LightGlue but leaves RAFT commented out; we include RAFT because
# it is the only GPU-capable non-rigid registrar and therefore the one that
# makes --use_gpu worth enabling.
COPY download_weights.py /tmp/download_weights.py
RUN python /tmp/download_weights.py \
 && rm /tmp/download_weights.py \
 && chmod -R a+rX "${TORCH_HOME}"

CMD ["python"]
