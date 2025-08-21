#define _GNU_SOURCE

// see: https://stackoverflow.com/questions/6083337/overriding-malloc-using-the-ld-preload-mechanism

#include <stdio.h>
#include <dlfcn.h>
#include <sys/time.h>
#include <stdatomic.h>


static char*   __user_fn = NULL;
static FILE*   __user_fd = NULL;
static char    __user_buf[4096];

static void  (*__user_free   )(void*)          = NULL;
static void* (*__user_malloc )(size_t)         = NULL;
static void* (*__user_calloc )(size_t, size_t) = NULL;
static void* (*__user_realloc)(void*,  size_t) = NULL;


static void  __malloc_initialize(void);
static void  __malloc_finalize  (void);


void free(void* p) {
    struct timeval cur;

    gettimeofday(&cur, NULL);

    if (__user_free)
        __user_free(p);

    if (__user_fd)
        fprintf(__user_fd, "%ld.%06ld free(%lx)\n",
                            cur.tv_sec,
                            cur.tv_usec,
                           (unsigned long)(p));
}


void* malloc(size_t sz) {
    struct timeval cur;

    gettimeofday(&cur, NULL);

    void* ret = __user_malloc ? __user_malloc(sz) : NULL;

    if (__user_fd)
        fprintf(__user_fd, "%ld.%06ld malloc(%lx) = %lx\n",
                            cur.tv_sec,
                            cur.tv_usec,
                            sz,
                           (unsigned long)(ret));

    return ret;
}


void* calloc(size_t n, size_t sz) {
    struct timeval cur;

    gettimeofday(&cur, NULL);

    void* ret = __user_calloc ? __user_calloc(n, sz) : NULL;

    if (__user_fd)
        fprintf(__user_fd, "%ld.%06ld calloc(%lx, %lx) = %lx\n",
                            cur.tv_sec,
                            cur.tv_usec,
                            n,
                            sz,
                           (unsigned long)(ret));

    return ret;
}


void* realloc(void* p, size_t sz) {
    struct timeval cur;

    gettimeofday(&cur, NULL);

    void* ret = __user_realloc ? __user_realloc(p, sz) : NULL;

    if (__user_fd)
        fprintf(__user_fd, "%ld.%06ld realloc(%lx, %lx) = %lx\n",
                            cur.tv_sec,
                            cur.tv_usec,
                           (unsigned long)(p),
                            sz,
                           (unsigned long)(ret));

    return ret;
}


void __temp_free(void* p) {
}


void* __temp_malloc(size_t sz) {
    static size_t temp_pos = 0;

    temp_pos += sz;

    if (temp_pos > sizeof(__user_buf)) {
        fprintf(stderr, "ERROR: temp_malloc: buffer overflow\n");
        return NULL;
    }

    return __user_buf + (temp_pos - sz);
}


void __attribute__((constructor)) __malloc_initialize(void) {
    // for dlsym
    __user_free    = __temp_free;
    __user_malloc  = __temp_malloc;

    char* (*_getenv )(const char*);

    // posix compatible
    // for glibc, one can simply use __libc_* functions
    void  (*_free   )(void*);
    void* (*_malloc )(size_t);
    void* (*_calloc )(size_t, size_t);
    void* (*_realloc)(void*,  size_t);

    if ((_getenv  = dlsym(RTLD_NEXT, "getenv" )) == NULL)
        goto dl_err;
    if ((_free    = dlsym(RTLD_NEXT, "free"   )) == NULL)
        goto dl_err;
    if ((_malloc  = dlsym(RTLD_NEXT, "malloc" )) == NULL)
        goto dl_err;
    if ((_calloc  = dlsym(RTLD_NEXT, "calloc" )) == NULL)
        goto dl_err;
    if ((_realloc = dlsym(RTLD_NEXT, "realloc")) == NULL)
        goto dl_err;

    __user_free    = _free;
    __user_malloc  = _malloc;
    __user_calloc  = _calloc;
    __user_realloc = _realloc;

    if ((__user_fn = _getenv("MALLOC_TRACE")) == NULL)
        __user_fn = "mtrace.log";
    if ((__user_fd = fopen(__user_fn, "wce")) == NULL)
        goto io_err;

    setvbuf(__user_fd, __user_buf, _IOFBF, sizeof(__user_buf));

    return;

dl_err:
    fprintf(stderr, "ERROR: dlsym: %s\n", dlerror());
    return;

io_err:
    fprintf(stderr, "ERROR: fopen: %s", __user_fn);
    perror ("");
    return;
}


void __attribute__((destructor)) __malloc_finalize(void) {
    if (__user_fd == NULL)
        return;

    fclose(__user_fd);
    __user_fd = NULL;
}