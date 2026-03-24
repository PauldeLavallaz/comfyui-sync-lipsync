import time, json, requests, os, tempfile
from pathlib import Path

# ─────────────── API KEY NODE ──────────────────────────────────────────────
class SyncApiKeyNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("SYNC_API_KEY",)
    RETURN_NAMES = ("api_key",)
    FUNCTION = "provide_api_key"
    CATEGORY = "Sync.so/Lipsync"

    def provide_api_key(self, api_key):
        return ({"api_key": api_key},)


# ─────────────── MAIN LIPSYNC NODE ─────────────────────────────────────────
class SyncLipsyncNode:
    """
    Unified node: accepts IMAGE frames + AUDIO directly.
    Uploads to sync.so API, polls for result, returns IMAGE frames.
    Connect output IMAGE to VHS_SaveVideo or any Save Video node.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key":  ("SYNC_API_KEY", {"forceInput": True}),
                "images":   ("IMAGE",),        # from Load Video (VHS) or any IMAGE source
                "audio":    ("AUDIO",),        # from Load Audio or any AUDIO source
                "fps":      ("FLOAT",  {"default": 25.0, "min": 1.0, "max": 60.0}),
                "model":    (["lipsync-2-pro", "lipsync-2", "lipsync-1.9.0-beta"],),
                "sync_mode":(["cut_off", "loop", "bounce", "silence", "remap"], {"default": "cut_off"}),
            }
        }

    RETURN_TYPES  = ("IMAGE", "VHS_AUDIO", "FLOAT", "STRING")
    RETURN_NAMES  = ("images", "audio", "frame_rate", "output_path")
    FUNCTION      = "lipsync"
    CATEGORY      = "Sync.so/Lipsync"

    def lipsync(self, api_key, images, audio, fps, model, sync_mode):
        import torch, numpy as np, cv2, soundfile as sf

        api_key_str = api_key.get("api_key", "")
        if not api_key_str:
            raise ValueError("sync.so API key is required")

        headers = {"x-api-key": api_key_str, "x-sync-source": "comfyui"}
        tmpdir  = tempfile.mkdtemp()

        # ── 1. Write video frames → temp MP4 ──────────────────────────────
        video_path = os.path.join(tmpdir, "input_video.mp4")
        frames_np  = (images.cpu().numpy() * 255).astype(np.uint8)  # (N,H,W,3)
        N, H, W, _ = frames_np.shape
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(video_path, fourcc, fps, (W, H))
        for i in range(N):
            writer.write(cv2.cvtColor(frames_np[i], cv2.COLOR_RGB2BGR))
        writer.release()
        print(f"[Sync] Video written: {N} frames @ {fps}fps → {video_path}")

        # ── 2. Write audio → temp WAV ─────────────────────────────────────
        audio_path = os.path.join(tmpdir, "input_audio.wav")
        waveform   = audio["waveform"]   # (1, C, T) or (C, T) tensor
        sample_rate = audio["sample_rate"]
        wv = waveform.squeeze(0).cpu().numpy()  # (C, T)
        if wv.shape[0] > 1:
            wv = wv.mean(axis=0)           # stereo → mono
        else:
            wv = wv[0]
        sf.write(audio_path, wv, sample_rate)
        print(f"[Sync] Audio written: sr={sample_rate} → {audio_path}")

        # ── 3. Submit to sync.so API ──────────────────────────────────────
        input_block = [{"type": "video"}, {"type": "audio"}]
        fields = [
            ("model",        model),
            ("sync_mode",    sync_mode),
            ("temperature",  "0.5"),
            ("active_speaker","false"),
            ("input",        json.dumps(input_block)),
        ]
        files = {
            "video": open(video_path, "rb"),
            "audio": open(audio_path, "rb"),
        }
        print("[Sync] Submitting job...")
        res = requests.post("https://api.sync.so/v2/generate", headers=headers, data=fields, files=files)
        files["video"].close(); files["audio"].close()

        if res.status_code != 200:
            raise RuntimeError(f"sync.so error {res.status_code}: {res.text[:400]}")

        job_id = res.json()["id"]
        print(f"[Sync] Job submitted: {job_id}")

        # ── 4. Poll until done ────────────────────────────────────────────
        status = None
        poll_response = None
        while status not in {"COMPLETED", "FAILED"}:
            time.sleep(5)
            poll_response = requests.get(f"https://api.sync.so/v2/generate/{job_id}", headers=headers)
            poll_response.raise_for_status()
            status = poll_response.json()["status"]
            print(f"[Sync] Status: {status}")

        if status != "COMPLETED":
            raise RuntimeError(f"sync.so job failed. Status: {status}")

        # ── 5. Download result ────────────────────────────────────────────
        result_data = poll_response.json()
        output_url  = result_data.get("outputUrl") or (result_data.get("result") or {}).get("outputUrl")
        if not output_url:
            raise RuntimeError("sync.so returned no outputUrl")

        output_path = os.path.join(tmpdir, f"sync_output_{job_id}.mp4")
        r = requests.get(output_url)
        r.raise_for_status()
        Path(output_path).write_bytes(r.content)
        print(f"[Sync] Downloaded result → {output_path}")

        # ── 6. Decode result video → IMAGE tensor ─────────────────────────
        cap = cv2.VideoCapture(output_path)
        out_frames = []
        result_fps = cap.get(cv2.CAP_PROP_FPS) or fps
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            out_frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0)
        cap.release()
        print(f"[Sync] Decoded {len(out_frames)} output frames")

        out_tensor = torch.from_numpy(np.array(out_frames))  # (N,H,W,3) float32

        # ── 7. Build VHS_AUDIO passthrough (original audio) ──────────────
        def vhs_audio_passthrough():
            return audio.get("waveform"), audio.get("sample_rate")

        return (out_tensor, lambda: (audio["waveform"], audio["sample_rate"]), result_fps, output_path)


# ────────────── REGISTER ──────────────────────────────────────────────────
NODE_CLASS_MAPPINGS = {
    "SyncApiKeyNode":   SyncApiKeyNode,
    "SyncLipsyncNode":  SyncLipsyncNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SyncApiKeyNode":   "sync.so – API Key",
    "SyncLipsyncNode":  "sync.so – Lipsync",
}

print("[Sync.so] Nodes loaded: API Key + Lipsync")
