libquelibrium.so — Chaos-Retrieval-Engine (vendored)

  sha256: de64b088d01ff86c0daf3e47a2fd55db97eae3997014e06231135afa5d5fcbdb
  Größe:  ~40 KB
  Source: ~/projekte/quelibrium/
          CMake: cd build && cmake .. && cmake --build .

  ABI (v0.3 — Hardware-IO erweitert):
    void*  init_quelibrium(int buffer_mb)
    void   free_quelibrium(void* ctx)
    void   get_system_state(void* ctx, float out[9])
    void   get_system_state_v2(void* ctx, float out[14])       ← NEU: +5 Hardware-Felder
    void   get_hardware_state(void* ctx, float out[5])          ← NEU: nur Sensoren
    void   enable_hardware_log(void* ctx, const char* path)     ← NEU: CSV-Log
    void   disable_hardware_log(void* ctx)                      ← NEU
    int    hardware_log_enabled(void* ctx)                      ← NEU
    void   pulse_system(void* ctx, float strength)
    void   apply_cortex_feedback(void* ctx, int error)
    float  get_cortex_bias(void* ctx, int mode)
    void   set_lorenz_params(void* ctx, double rho, double sigma, double beta, double reason)
    void   get_lorenz_params(void* ctx, double out[6])
    float  get_lyapunov_estimate(void* ctx)
    int    get_fold_state(void* ctx)
    int    get_topology(void* ctx)
    void   set_topology(void* ctx, int mode)
    float  get_coupling_strength(void* ctx)
    float  get_current_entropy(void* ctx)
    double* get_lorenz_state_bytes(void* ctx)
    double* spectral_block_search(void* ctx, int block, int stride, float threshold)

  Update-Prozedur:
    1. cd ~/projekte/quelibrium/build && cmake --build .
    2. cp libquelibrium.so <collect2>/src/collect/retrieval/lib/
    3. sha256sum <collect2>/src/collect/retrieval/lib/libquelibrium.so > dieses README aktualisieren
