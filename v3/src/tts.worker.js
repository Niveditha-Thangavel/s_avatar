import { KokoroTTS, TextSplitterStream } from 'kokoro-js';
import { env } from '@huggingface/transformers';

// Configure transformers.js environment for browser environments
env.allowLocalModels = false;
env.allowRemoteModels = true;

// Enable multi-threading for ONNX Runtime WASM execution
if (self.navigator && self.navigator.hardwareConcurrency) {
  env.backends.onnx.numThreads = Math.min(4, self.navigator.hardwareConcurrency);
} else {
  env.backends.onnx.numThreads = 4;
}

let ttsInstance = null;

self.onmessage = async (event) => {
  const { type, data } = event.data;

  if (type === 'load') {
    const { modelId, dtype, device } = data;
    try {
      self.postMessage({ type: 'status', data: 'loading' });
      
      ttsInstance = await KokoroTTS.from_pretrained(modelId, {
        dtype: dtype || 'q8',
        device: device || 'wasm',
        progress_callback: (progressData) => {
          if (progressData.status === 'progress') {
            self.postMessage({
              type: 'progress',
              data: {
                file: progressData.file,
                progress: progressData.progress,
                loaded: progressData.loaded,
                total: progressData.total
              }
            });
          }
        }
      });

      self.postMessage({ type: 'status', data: 'ready' });
    } catch (error) {
      console.error('[Worker] Failed to load Kokoro model:', error);
      self.postMessage({ type: 'error', data: `Load failed: ${error.message}` });
    }
  }

  else if (type === 'generate') {
    const { text, voice, speed } = data;
    if (!ttsInstance) {
      self.postMessage({ type: 'error', data: 'TTS model is not loaded yet.' });
      return;
    }

    try {
      self.postMessage({ type: 'status', data: 'generating' });

      // Create a TextSplitterStream, write text to it, and close it.
      // This correctly flushes all sentences and closes the stream to prevent hanging.
      const splitter = new TextSplitterStream();
      const stream = ttsInstance.stream(splitter, { 
        voice: voice || 'af_heart',
        speed: speed || 1.0 
      });

      splitter.push(text);
      splitter.close();

      for await (const chunk of stream) {
        // chunk structure: { text, phonemes, audio }
        // where audio is: RawAudio { data: Float32Array, sampling_rate: number }
        if (chunk && chunk.audio) {
          // Transfer the Float32Array buffer to avoid copying memory across thread boundaries
          const audioData = chunk.audio.audio;
          self.postMessage({
            type: 'chunk',
            data: {
              text: chunk.text,
              phonemes: chunk.phonemes,
              audio: audioData,
              samplingRate: chunk.audio.sampling_rate
            }
          }, [audioData.buffer]);
        }
      }

      self.postMessage({ type: 'status', data: 'complete' });
    } catch (error) {
      console.error('[Worker] Synthesis error:', error);
      self.postMessage({ type: 'error', data: `Synthesis failed: ${error.message}` });
    }
  }
};
