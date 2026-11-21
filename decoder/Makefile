# Builds wsm_decode (decoder front end) and wsm_symbols (test message encoder). Needs: gcc, libfftw3-dev
CC ?= gcc
CFLAGS ?= -O3 -std=gnu17 -Wall -Wno-unused-parameter -I.
LIBS = -lfftw3f -lm
OBJS = wsprd/wsprd.o wsprd/wsprd_utils.o wsprd/tab.o wsprd/fano.o wsprd/nhash.o wsprd/wsprsim_utils.o

all: wsm_decode wsm_symbols

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

wsm_decode: wsm_decode.o $(OBJS)
	$(CC) -o $@ $^ $(LIBS)

wsm_symbols: wsm_symbols.o $(OBJS)
	$(CC) -o $@ $^ $(LIBS)

clean:
	rm -f *.o wsprd/*.o wsm_decode wsm_symbols
