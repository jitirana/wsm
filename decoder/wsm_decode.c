/*
  wsm_decode.c - command line front end of the WSPR decoder (wsprd, K1JT / K9AN) used by WSM.

  Reads one 2-minute recording: complex float32 samples (I,Q interleaved) at 375 samples/s, baseband centred on
  dial frequency + 1500 Hz, 120 s = 45000 samples, and prints one JSON line per decoded spot, then a summary line.

  Usage: wsm_decode -f DIAL_HZ [-c RX_CALL] [-l RX_GRID] [-H] FILE.iq
  Run it in a writable directory: the decoder keeps its FFTW wisdom (and, with -H, its hash table) in the current directory.

  The decoder sources in this directory come from https://github.com/Guenael/rtlsdr-wsprd (wsprd/), GPL v3.
*/
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wsprd/wsprd.h"

#define SIGNAL_SECONDS 120
#define SIGNAL_RATE    375
#define NSAMP          (SIGNAL_SECONDS * SIGNAL_RATE)

int main(int argc, char **argv) {
    struct decoder_options opt;
    memset(&opt, 0, sizeof(opt));
    opt.npasses = 2;
    opt.subtraction = 1;
    opt.usehashtable = 0;
    opt.quickmode = 0;
    snprintf(opt.rcall, sizeof(opt.rcall), "%s", "N0CALL");
    snprintf(opt.rloc, sizeof(opt.rloc), "%s", "AA00aa");
    const char *file = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-f") && i + 1 < argc) opt.freq = (int)atof(argv[++i]);
        else if (!strcmp(argv[i], "-c") && i + 1 < argc) snprintf(opt.rcall, sizeof(opt.rcall), "%s", argv[++i]);
        else if (!strcmp(argv[i], "-l") && i + 1 < argc) snprintf(opt.rloc, sizeof(opt.rloc), "%s", argv[++i]);
        else if (!strcmp(argv[i], "-H")) opt.usehashtable = 1;
        else file = argv[i];
    }
    if (!file || opt.freq <= 0) {
        fprintf(stderr, "usage: %s -f DIAL_HZ [-c RX_CALL] [-l RX_GRID] [-H] FILE.iq\n", argv[0]);
        return 2;
    }
    FILE *fd = fopen(file, "rb");
    if (!fd) {
        fprintf(stderr, "cannot open %s\n", file);
        return 2;
    }
    static float raw[2 * NSAMP];
    static float idat[NSAMP], qdat[NSAMP];
    size_t nread = fread(raw, sizeof(float), 2 * NSAMP, fd);
    fclose(fd);
    int n = (int)(nread / 2);
    float peak = 1e-24f;
    for (int i = 0; i < n; i++) {
        idat[i] = raw[2 * i];
        qdat[i] = raw[2 * i + 1];
        if (fabsf(idat[i]) > peak) peak = fabsf(idat[i]);
        if (fabsf(qdat[i]) > peak) peak = fabsf(qdat[i]);
    }
    float k = 0.5f / peak;                       // -6 dB of full scale, like rtlsdr-wsprd
    for (int i = 0; i < n; i++) {
        idat[i] *= k;
        qdat[i] *= k;
    }
    static struct decoder_results res[50];
    int nres = 0;
    wspr_decode(idat, qdat, NSAMP, opt, res, &nres);
    for (int i = 0; i < nres; i++) {
        printf("{\"freq_mhz\":%.6f,\"snr\":%.0f,\"dt\":%.1f,\"drift\":%d,\"call\":\"%s\",\"loc\":\"%s\",\"pwr\":\"%s\","
               "\"sync\":%.2f,\"cycles\":%d,\"jitter\":%d}\n",
               res[i].freq, res[i].snr, res[i].dt, (int)res[i].drift, res[i].call, res[i].loc, res[i].pwr,
               res[i].sync, res[i].cycles, res[i].jitter);
    }
    printf("{\"summary\":true,\"spots\":%d,\"samples\":%d}\n", nres, n);
    return 0;
}
