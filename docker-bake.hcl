# AstroAI container stack — build with: docker buildx bake

variable "REGISTRY" {
  default = "images.canfar.net"
}

variable "OWNER" {
  default = "astroai"
}

variable "TAG" {
  default = "local"
}

variable "PYTHON_VERSION" {
  default = "3.13"
}

# Default build. notebook, marimo, and vscode stay until they are removed.
# Do not `docker buildx bake --push` this group: specproc includes pPXF and
# MOOG and must not land in the public astroai project. Use group "release"
# or `make release-push`.
group "default" {
  targets = [
    "base",
    "studio",
    "improc-studio",
    "specproc",
    "specproc-studio",
    "notebook",
    "marimo",
    "vscode",
  ]
}

# Public Harbor release. improc is the parent of improc-studio.
group "release" {
  targets = ["base", "improc", "studio", "improc-studio", "notebook", "marimo", "vscode"]
}

group "improc" {
  targets = ["improc", "improc-studio"]
}

# Group spectroscopy stack. Not part of the public astroai catalog.
group "specproc" {
  targets = ["specproc", "specproc-studio"]
}

# Untagged bake parent. Never a Harbor image.
target "python" {
  context    = "./dockerfiles/python"
  dockerfile = "Dockerfile"
  args = {
    PYTHON_VERSION = "${PYTHON_VERSION}"
  }
}

target "base" {
  context    = "."
  dockerfile = "dockerfiles/base/Dockerfile"
  contexts = {
    "astroai-python:${PYTHON_VERSION}" = "target:python"
  }
  tags = ["${REGISTRY}/${OWNER}/base:${TAG}"]
  args = {
    PYTHON_VERSION = "${PYTHON_VERSION}"
  }
}

target "_interface" {
  context = "."
  contexts = {
    "${REGISTRY}/${OWNER}/base:${TAG}" = "target:base"
  }
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "base"
    TAG       = "${TAG}"
  }
}

target "terminal" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/terminal/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/terminal:${TAG}"]
}

target "notebook" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/notebook/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/notebook:${TAG}"]
}

target "vscode" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/vscode/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/vscode:${TAG}"]
}

target "marimo" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/marimo/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/marimo:${TAG}"]
}

target "openresearch" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/openresearch/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/openresearch:${TAG}"]
}

target "openscience" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/openscience/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/openscience:${TAG}"]
}

target "studio" {
  inherits   = ["_interface"]
  dockerfile = "dockerfiles/studio/Dockerfile"
  tags       = ["${REGISTRY}/${OWNER}/studio:${TAG}"]
}

# Ray cluster images
# - ray-base: slim (from python) → ray-worker
# - ray-manager: fat (from base) + Ray runtime
target "ray-base" {
  context    = "."
  dockerfile = "dockerfiles/ray-base/Dockerfile"
  contexts = {
    "astroai-python:${PYTHON_VERSION}" = "target:python"
  }
  tags = ["${REGISTRY}/${OWNER}/ray-base:${TAG}"]
  args = {
    PYTHON_VERSION = "${PYTHON_VERSION}"
  }
}

target "ray-worker" {
  context    = "."
  dockerfile = "dockerfiles/ray-worker/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/ray-base:${TAG}" = "target:ray-base"
  }
  tags = ["${REGISTRY}/${OWNER}/ray-worker:${TAG}"]
  args = {
    REGISTRY = "${REGISTRY}"
    OWNER    = "${OWNER}"
    TAG      = "${TAG}"
  }
}

target "ray-manager" {
  context    = "."
  dockerfile = "dockerfiles/ray-manager/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/base:${TAG}" = "target:base"
  }
  tags = ["${REGISTRY}/${OWNER}/ray-manager:${TAG}"]
  args = {
    REGISTRY = "${REGISTRY}"
    OWNER    = "${OWNER}"
    TAG      = "${TAG}"
  }
}

# Headless astronomy image-processing CLIs (FITS/HDF5). Parent of improc-studio.
target "improc" {
  context    = "."
  dockerfile = "dockerfiles/improc/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/base:${TAG}" = "target:base"
  }
  tags = ["${REGISTRY}/${OWNER}/improc:${TAG}"]
  args = {
    REGISTRY = "${REGISTRY}"
    OWNER    = "${OWNER}"
    TAG      = "${TAG}"
  }
}

# Studio on improc. Same Dockerfile as studio; improc is the default Python.
target "improc-studio" {
  context    = "."
  dockerfile = "dockerfiles/studio/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/improc:${TAG}" = "target:improc"
  }
  tags = ["${REGISTRY}/${OWNER}/improc-studio:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "improc"
    TAG       = "${TAG}"
  }
}

# Legacy. Not in the default build.
target "improc-terminal" {
  context    = "."
  dockerfile = "dockerfiles/terminal/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/improc:${TAG}" = "target:improc"
  }
  tags = ["${REGISTRY}/${OWNER}/improc-terminal:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "improc"
    TAG       = "${TAG}"
  }
}

# Headless spectroscopy stack. Group image: do not push to public astroai.
target "specproc" {
  context    = "."
  dockerfile = "dockerfiles/specproc/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/base:${TAG}" = "target:base"
  }
  tags = ["${REGISTRY}/${OWNER}/specproc:${TAG}"]
  args = {
    REGISTRY = "${REGISTRY}"
    OWNER    = "${OWNER}"
    TAG      = "${TAG}"
  }
}

# Studio on specproc. Same Dockerfile as studio; specproc is the default Python.
target "specproc-studio" {
  context    = "."
  dockerfile = "dockerfiles/studio/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/specproc:${TAG}" = "target:specproc"
  }
  tags = ["${REGISTRY}/${OWNER}/specproc-studio:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "specproc"
    TAG       = "${TAG}"
  }
}

# Legacy. Not in the default build.
target "specproc-terminal" {
  context    = "."
  dockerfile = "dockerfiles/terminal/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/specproc:${TAG}" = "target:specproc"
  }
  tags = ["${REGISTRY}/${OWNER}/specproc-terminal:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "specproc"
    TAG       = "${TAG}"
  }
}

target "specproc-notebook" {
  context    = "."
  dockerfile = "dockerfiles/specproc-notebook/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/specproc:${TAG}" = "target:specproc"
  }
  tags = ["${REGISTRY}/${OWNER}/specproc-notebook:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "specproc"
    TAG       = "${TAG}"
  }
}

# JupyterLab on improc — default kernel is the science venv.
target "improc-notebook" {
  context    = "."
  dockerfile = "dockerfiles/improc-notebook/Dockerfile"
  contexts = {
    "${REGISTRY}/${OWNER}/improc:${TAG}" = "target:improc"
  }
  tags = ["${REGISTRY}/${OWNER}/improc-notebook:${TAG}"]
  args = {
    REGISTRY  = "${REGISTRY}"
    OWNER     = "${OWNER}"
    BASE_NAME = "improc"
    TAG       = "${TAG}"
  }
}
