FROM ubuntu:26.04

LABEL ai.ephemeral=true

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
	bash \
	ca-certificates \
	curl \
	coreutils \
	findutils \
	tar \
	gzip \
	git \
	golang-go \
	python3 \
	gcc \
	g++ \
	make \
	autoconf \
	automake \
	autoconf-archive \
	autotools-dev \
	libedit-dev \
	libjemalloc-dev \
	libncurses-dev \
	libpcre2-dev \
	libtool \
	pkg-config \
	python3-docutils \
	python3-sphinx \
	cpio \
	libunwind-dev \
	linux-perf \
	procps \
	sysstat \
	time \
 && rm -rf /var/lib/apt/lists/*

RUN case "$(dpkg --print-architecture)" in \
	amd64) oha_asset=oha-linux-amd64; oha_sha256=620bb9e16fb53eabc9a3fc45f88bdb41fefa3fee5c05e75892011ce320391716 ;; \
	arm64) oha_asset=oha-linux-arm64; oha_sha256=99a790eb8c3e0feaca974bd6b32f0f8d4426a0c5b289f39e833e5b2c7529cd39 ;; \
	*) echo "unsupported oha architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
	esac \
 && curl -fsSL -o /usr/local/bin/oha "https://github.com/hatoo/oha/releases/download/v1.16.0/$oha_asset" \
 && echo "$oha_sha256  /usr/local/bin/oha" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/oha \
 && oha --version | grep -Fx 'oha 1.16.0'
