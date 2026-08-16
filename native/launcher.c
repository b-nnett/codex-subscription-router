#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int executable_directory(char *destination, size_t capacity) {
    uint32_t size = (uint32_t)capacity;
    if (_NSGetExecutablePath(destination, &size) != 0) {
        errno = ENAMETOOLONG;
        return -1;
    }
    char *separator = strrchr(destination, '/');
    if (separator == NULL) {
        errno = EINVAL;
        return -1;
    }
    *separator = '\0';
    return 0;
}

int main(int argc, char **argv) {
    char directory[PATH_MAX];
    if (executable_directory(directory, sizeof(directory)) != 0) {
        perror("Codex Subscription Router launcher");
        return EXIT_FAILURE;
    }

    char executable[PATH_MAX];
    if (snprintf(executable, sizeof(executable), "%s/ChatGPT", directory) >=
        (int)sizeof(executable)) {
        fprintf(stderr, "Codex Subscription Router launcher: executable path is too long\n");
        return EXIT_FAILURE;
    }

    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') {
        fprintf(stderr, "Codex Subscription Router launcher: HOME is not set\n");
        return EXIT_FAILURE;
    }

#ifdef CODEX_MUX_ISOLATE_SQLITE
    if (setenv("CODEX_MUX_ISOLATE_SQLITE", "1", 1) != 0) {
        perror("Codex Subscription Router launcher");
        return EXIT_FAILURE;
    }
#endif

    char profile[PATH_MAX];
    if (snprintf(profile, sizeof(profile),
                 "--user-data-dir=%s/Library/Application Support/Codex Subscription Router",
                 home) >= (int)sizeof(profile)) {
        fprintf(stderr, "Codex Subscription Router launcher: profile path is too long\n");
        return EXIT_FAILURE;
    }

    char **arguments = calloc((size_t)argc + 2, sizeof(*arguments));
    if (arguments == NULL) {
        perror("Codex Subscription Router launcher");
        return EXIT_FAILURE;
    }
    arguments[0] = executable;
    arguments[1] = profile;
    for (int index = 1; index < argc; index++) {
        arguments[index + 1] = argv[index];
    }
    arguments[argc + 1] = NULL;

    execv(executable, arguments);
    perror("Codex Subscription Router launcher");
    free(arguments);
    return EXIT_FAILURE;
}
