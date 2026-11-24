# Builds wsprd_wsjtx (the WSJT-X wsprd, default decoder), wsm_decode (older decoder, fallback) and wsm_symbols (test message encoder).
# Needs: gcc, libfftw3-dev
CC ?= gcc
CFLAGS ?= -O3 -std=gnu17 -Wall -Wno-unused-parameter -I.
# the WSJT-X sources are used as they are: no warnings from them
wsjtx/%.o: wsjtx/%.c
	$(CC) -O3 -std=gnu17 -w -Iwsjtx -c $< -o $@
LIBS = -lfftw3f -lm
OBJS = wsprd/wsprd.o wsprd/wsprd_utils.o wsprd/tab.o wsprd/fano.o wsprd/nhash.o wsprd/wsprsim_utils.o

WOBJS = wsjtx/wsprd.o wsjtx/wsprd_utils.o wsjtx/tab.o wsjtx/fano.o wsjtx/jelinek.o wsjtx/nhash.o wsjtx/wsprsim_utils.o wsjtx/osd_stub.o

all: wsprd_wsjtx wsm_decode wsm_symbols

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

wsprd_wsjtx: $(WOBJS)
	$(CC) -o $@ $^ $(LIBS)

wsm_decode: wsm_decode.o $(OBJS)
	$(CC) -o $@ $^ $(LIBS)

wsm_symbols: wsm_symbols.o $(OBJS)
	$(CC) -o $@ $^ $(LIBS)

clean:
	rm -f *.o wsprd/*.o wsjtx/*.o wsprd_wsjtx wsm_decode wsm_symbols
