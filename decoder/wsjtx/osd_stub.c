/* WSM: the ordered-statistics decoder of WSJT-X (option -o, Fortran) is not built; wsprd only calls it with -o, which WSM never passes.
   This stub keeps the link simple (no Fortran compiler needed on the Raspberry Pi). */
void osdwspr_(float *fsymbs, unsigned char *apmask, int *ndepth, unsigned char *cw, int *nhardmin, float *dmin) {
    (void)fsymbs; (void)apmask; (void)ndepth; (void)cw;
    *nhardmin = 99;
    *dmin = 1e9f;
}
