#!/usr/bin/env python3
"""Pre-download the torch/kornia weights VALIS pulls at first use.

Run at image build time so pipeline runs need no network egress. Mirrors
upstream's docker/docker_download_weights.py, plus RAFT, which upstream leaves
commented out -- RAFTWarper is the only GPU-capable non-rigid registrar, so a
--use_gpu run needs those weights present.

Instantiating these on a build machine with no GPU is fine: each class falls
back to CPU, and the weights land in $TORCH_HOME either way.
"""
import sys

from valis import feature_detectors, feature_matcher, non_rigid_registrars


def main() -> int:
    print("Downloading DiskFD weights", flush=True)
    disk_fd = feature_detectors.DiskFD()

    print("Downloading DeDoDeFD weights", flush=True)
    dedode_fd = feature_detectors.DeDoDeFD()

    print("Downloading LightGlue weights", flush=True)
    feature_matcher.LightGlueMatcher(disk_fd)
    feature_matcher.LightGlueMatcher(dedode_fd)

    print("Downloading RAFT weights", flush=True)
    non_rigid_registrars.RAFTWarper()

    print("All weights downloaded", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
