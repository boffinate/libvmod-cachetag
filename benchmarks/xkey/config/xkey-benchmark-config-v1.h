/*
 * Deliberately minimal xkey build configuration, version 1.
 *
 * This versioned source artifact is copied to config/config.h in the Docker
 * build directory. It is placed before both the xkey source and Vinyl build
 * include paths. It prevents the unsupported third-party build from consuming
 * the generated Vinyl config.h merely because vmod_xkey.c includes "config.h".
 * The xkey source compiled by the benchmark does not require configured feature
 * macros beyond its explicit compiler and Varnish API flags.
 */
#ifndef CACHETAG_XKEY_BUILD_CONFIG_H
#define CACHETAG_XKEY_BUILD_CONFIG_H

#define PACKAGE "varnish-modules"
#define PACKAGE_NAME "varnish-modules"
#define PACKAGE_STRING "varnish-modules 0.28.0"
#define PACKAGE_TARNAME "varnish-modules"
#define PACKAGE_VERSION "0.28.0"
#define VERSION "0.28.0"

#endif
