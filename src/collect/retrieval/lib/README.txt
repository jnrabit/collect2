libquelibrium.so — Chaos-Retrieval-Engine (vendored)

  sha256: d7beab214d17da2fa498dab4fee5df75f60600e7a214c668dcc5a98218464a47
  Größe:  ~48 KB
  Source: ~/projekte/quelibrium/
          CMake: cd build && cmake .. && cmake --build .

  ABI:
    void*  init_quelibrium(int buffer_mb)
    void   free_quelibrium(void* ctx)
    void   get_system_state(void* ctx, float out[9])
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
