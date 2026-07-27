FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
	bash \
	ca-certificates \
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
