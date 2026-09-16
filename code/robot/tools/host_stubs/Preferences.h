#ifndef MOF_HOST_PREFERENCES_H
#define MOF_HOST_PREFERENCES_H

#include <cstddef>
#include <cstdint>

class Preferences {
public:
    bool begin(const char *, bool) { return false; }
    size_t putBytes(const char *, const void *, size_t) { return 0; }
    size_t getBytesLength(const char *) { return 0; }
    size_t getBytes(const char *, void *, size_t) { return 0; }
    void end() {}
};

#endif
