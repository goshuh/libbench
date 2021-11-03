#include <stdlib.h>
#include <mcheck.h>


void __attribute__((destructor)) __mtrace_off () {
    muntrace();
}

void __attribute__((constructor)) __mtrace_on () {
    atexit(&__mtrace_off);
    mtrace();
}
