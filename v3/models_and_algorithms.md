# Models and Algorithms: 3D Digital Human

This document outlines the machine learning models and mathematical/procedural algorithms used in the 3D Digital Human project.

---

## 1. Machine Learning & Assets Models

### A. Kokoro-82M-v1.0 TTS Model (`onnx-community/Kokoro-82M-v1.0-ONNX`)
*   **Purpose**: Local Text-to-Speech synthesis in the browser.
*   **Architecture**: StyleTTS2-inspired neural network structure consisting of:
    *   **Phoneme/Text Encoder**: Transformer blocks mapping input phonetic tokens to latent features.
    *   **Style Encoder**: Generates a style vector matching selected voice targets (`af_heart`, `am_adam`, etc.).
    *   **Decoder**: A flow-matching/diffusion decoder that translates text representations and style vectors into mel-spectrogram grids.
    *   **Vocoder**: A GAN-based generator that synthesizes raw Float32 audio samples (PCM) at 24kHz.
*   **Size**: Reduced from ~170MB (standard FP32) down to ~45MB using 4-bit (`q4`) or 8-bit (`q8`) integer quantization.

### B. Rigged Humanoid 3D Avatar (`avatar_head.glb`)
*   **Purpose**: 3D character visualization.
*   **Structure**: GLTF/GLB binary file format.
*   **Armature/Skeleton**: Standard Ready Player Me / Mixamo armature rigging with joint groups (`LeftArm`, `RightArm`, `LeftForeArm`, `LeftHand`, `Neck`, `Head`, `LeftEye`, `RightEye`).
*   **Morph Targets (Blendshapes)**: Oculus/ARKit-standard facial vertex targets (`mouthOpen`, `eyeBlinkLeft`, `mouthSmileRight`, etc.) used for speech lip-syncing and emotional expressions.

---

## 2. Algorithms & Mathematical Methods

### A. Text Processing & Tokenization
*   **G2P (Grapheme-to-Phoneme) Conversion**: Parses incoming written text strings (graphemes) and translates them into phonetic symbols (IPA tokens like `həˈloʊ` for "hello") using phonetic lookup tables and rule-based pronunciation engines.

### B. Signal Processing & Analysis
*   **Root Mean Square (RMS) Volume Extraction**: Computes the real-time average amplitude of speech playback buffers:
    $$V_{\text{RMS}} = \sqrt{\frac{1}{N} \sum_{i=1}^{N} s_i^2}$$
    where $s_i$ represents the individual float audio samples in a frame of size $N$. This RMS value drives the speaking gesture and head nodding intensities.

### C. Skeletal & Facial Animation
*   **Low-Pass Filter (Exponential Smoothing)**: Filters raw RMS volume inputs to prevent jerky or sudden jumps in character sways:
    $$y_t = y_{t-1} + (x_t - y_{t-1}) \times (1 - e^{-k \cdot dt})$$
    where $x_t$ is current volume, $y_t$ is smoothed output, $k$ is filter speed constant, and $dt$ is frame delta time.
*   **Harmonic Oscillators (Sine/Cosine Waves)**: Drives procedural, natural sways for breathing and idling:
    $$\theta_{\text{sway}} = \sin(\text{time} \cdot \omega) \times A$$
    where $\omega$ is angular velocity (speed) and $A$ is the rotation amplitude.
*   **Viseme Coarticulation (Linear Interpolation / LERP)**: Blends mouth morph target values smoothly between current weights and target viseme targets during render draws:
    $$W_t = W_{t-1} + (T - W_{t-1}) \times \alpha$$
    where $W$ is active target weight, $T$ is target viseme weight, and $\alpha$ is step lerp speed factor.

### D. Biological Simulation
*   **Saccadic Gaze Algorithm**: Employs a randomized interval timer with a Gaussian coordinate offset to simulate micro-saccades (rapid, involuntary eye movements) of a living eye.
*   **Markovian Blink State Machine**: A multi-state logical flow (`idle` $\rightarrow$ `closing` $\rightarrow$ `opening` $\rightarrow$ `waiting-double` $\rightarrow$ `double-closing` $\rightarrow$ `double-opening`) with a 15% random distribution gate to trigger single or natural double blinks.
