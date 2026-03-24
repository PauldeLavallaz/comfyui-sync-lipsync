import time, json, requests, os, tempfile, subprocess
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


# ─────────────── LIPSYNC NODE (VIDEO in / VIDEO out) ───────────────────────
class SyncLipsyncNode:
    """
    Accepts native VIDEO + AUDIO, outputs native VIDEO.
    Works with ComfyUI's native Load Video and Save Video nodes.
    VIDEO type = file path string (as used by native ComfyUI video nodes).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key":   ("SYNC_API_KEY", {"forceInput": True}),
                "video":     ("VIDEO",),
                "audio":     ("AUDIO",),
                "model":     (["lipsync-2-pro", "lipsync-2", "lipsync-1.9.0-beta"],),
                "sync_mode": (["cut_off", "loop", "bounce", "silence", "remap"], {"default": "cut_off"}),
            }
        }

    RETURN_TYPES  = ("VIDEO",)
    RETURN_NAMES  = ("video",)
    FUNCTION      = "lipsync"
    CATEGORY      = "Sync.so/Lipsync"

    def lipsync(self, api_key, video, audio, model, sync_mode):
        import soundfile as sf
        import numpy as np

        api_key_str = api_key.get("api_key", "")
        if not api_key_str:
            raise ValueError("sync.so API key is required")

        headers = {"x-api-key": api_key_str, "x-sync-source": "comfyui"}
        tmpdir  = tempfile.mkdtemp()

        # ── 1. Resolve video path ─────────────────────────────────────────
        # Native ComfyUI VIDEO type is a file path string
        if isinstance(video, str):
            video_path = video
        elif isinstance(video, dict) and "path" in video:
            video_path = video["path"]
        elif hasattr(video, "video_path"):
            video_path = video.video_path
        else:
            raise ValueError(f"Cannot resolve video path from type: {type(video)}")

        if not os.path.exists(video_path):
            # Try ComfyUI input folder
            import folder_paths
            candidate = os.path.join(folder_paths.get_input_directory(), video_path)
            if os.path.exists(candidate):
                video_path = candidate
            else:
                raise ValueError(f"Video file not found: {video_path}")

        print(f"[Sync] Video input: {video_path}")

        # ── 2. Write audio → temp WAV ─────────────────────────────────────
        audio_path = os.path.join(tmpdir, "input_audio.wav")
        waveform    = audio["waveform"]    # tensor (1, C, T) or (C, T)
        sample_rate = audio["sample_rate"]
        wv = waveform.squeeze(0).cpu().numpy()
        if len(wv.shape) == 2 and wv.shape[0] > 1:
            wv = wv.mean(axis=0)
        elif len(wv.shape) == 2:
            wv = wv[0]
        sf.write(audio_path, wv, sample_rate)
        print(f"[Sync] Audio written: sr={sample_rate}")

        # ── 3. Submit to sync.so API ──────────────────────────────────────
        input_block = [{"type": "video"}, {"type": "audio"}]
        fields = [
            ("model",         model),
            ("sync_mode",     sync_mode),
            ("temperature",   "0.5"),
            ("active_speaker","false"),
            ("input",         json.dumps(input_block)),
        ]
        files = {
            "video": open(video_path, "rb"),
            "audio": open(audio_path, "rb"),
        }
        print("[Sync] Submitting job...")
        res = requests.post("https://api.sync.so/v2/generate", headers=headers, data=fields, files=files)
        files["video"].close()
        files["audio"].close()

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

        # Save to ComfyUI output folder
        try:
            import folder_paths
            output_dir = folder_paths.get_output_directory()
        except Exception:
            output_dir = tmpdir

        output_filename = f"sync_lipsync_{job_id}.mp4"
        output_path     = os.path.join(output_dir, output_filename)

        r = requests.get(output_url)
        r.raise_for_status()
        Path(output_path).write_bytes(r.content)
        print(f"[Sync] Result saved → {output_path}")

        return (output_path,)


# ────────────── REGISTER ──────────────────────────────────────────────────
NODE_CLASS_MAPPINGS = {
    "SyncApiKeyNode":  SyncApiKeyNode,
    "SyncLipsyncNode": SyncLipsyncNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SyncApiKeyNode":  "sync.so – API Key",
    "SyncLipsyncNode": "sync.so – Lipsync",
}

print("[Sync.so] Nodes loaded.")
