# Local runner image for the unmodified upstream xkey 0.28.0 suite.
#
# Varnish-modules 0.28.0 declares Varnish 9.0.0 as its supported API floor.
# Keep this Dockerfile reviewed and hash it in the suite manifest. Build it
# locally as `cachetag-xkey-varnish-9.0.0`; the runner never pulls or builds an
# image implicitly.
FROM varnish:9.0.0

ENV DEBIAN_FRONTEND=noninteractive
USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
	autoconf \
	automake \
	autoconf-archive \
	build-essential \
	git \
	libtool \
	pkg-config \
	python3-docutils \
	python3-sphinx \
	varnish-dev=9.0.0-1~trixie \
 && rm -rf /var/lib/apt/lists/*
