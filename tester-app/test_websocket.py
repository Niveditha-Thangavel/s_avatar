import asyncio
import json
import websockets
import numpy as np
import soundfile as sf
import torchaudio
import torch

async def test():
    uri = "ws://localhost:8765/ws/s2s"
    print(f"Connecting to S2S WebSocket at {uri}...")
    try:
        async with websockets.connect(uri) as ws:
            print("Connected! Sending 'start' control message...")
            
            # Send init message
            init_msg = {
                "type": "start",
                "lang": "hi-IN",
                "session_id": "test_client_session"
            }
            await ws.send(json.dumps(init_msg))
            print("Sent init message. Loading ref_audio.wav...")
            
            # Load and resample ref_audio.wav
            data, sr = sf.read('s_avatar/V3/server/ref_audio.wav')
            waveform = torch.tensor(data, dtype=torch.float32).unsqueeze(0)
            resampler = torchaudio.transforms.Resample(sr, 16000)
            resampled_waveform = resampler(waveform)
            pcm_f32 = resampled_waveform.squeeze(0).numpy()
            pcm_i16 = (pcm_f32 * 32767).astype(np.int16)
            audio_bytes = pcm_i16.tobytes()
            
            print(f"Sending {len(audio_bytes)} bytes of real speech PCM audio...")
            # Send audio in chunks of 4096 bytes (every 128ms)
            chunk_size = 4096
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i:i+chunk_size]
                await ws.send(chunk)
                await asyncio.sleep(0.1)
                
            print("Finished sending audio. Sending 'stop' control message...")
            await ws.send(json.dumps({"type": "stop"}))
            
            # Wait for responses
            print("Awaiting responses (timeout 15s)...")
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                    if isinstance(msg, bytes):
                        print(f"Received binary audio chunk: {len(msg)} bytes")
                    else:
                        data = json.loads(msg)
                        print(f"Received JSON message: {json.dumps(data, indent=2, ensure_ascii=False)}")
                except asyncio.TimeoutError:
                    print("Timeout reached (no more messages).")
                    break
                    
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
