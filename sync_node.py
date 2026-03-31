import time, json, requests, os, tempfile
from pathlib import Path
from os.path import getsize

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


# ─────────────── VIDEO INPUT NODE ──────────────────────────────────────────
class SyncVideoInputNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "video":      ("VIDEO",),
                "video_path": ("STRING", {"default": ""}),
                "video_url":  ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("SYNC_VIDEO",)
    RETURN_NAMES = ("video",)
    FUNCTION = "provide_video"
    CATEGORY = "Sync.so/Lipsync"

    def provide_video(self, video=None, video_path="", video_url=""):
        if video is not None:
            return self._resolve_video(video)

        if video_path and os.path.exists(video_path):
            return ({"video_path": video_path, "type": "path"},)

        if video_url:
            return ({"video_url": video_url, "type": "url"},)

        return ({"video_path": "", "type": "path"},)

    def _resolve_video(self, video):
        # New ComfyUI API: VideoFromFile has save_to()
        if hasattr(video, 'save_to'):
            tmpdir = tempfile.mkdtemp()
            temp_path = os.path.join(tmpdir, "input_video.mp4")
            video.save_to(temp_path)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                print(f"[Sync] VideoFromFile saved to: {temp_path}")
                return ({"video_path": temp_path, "type": "path"},)
            raise ValueError("Failed to save VideoFromFile")

        # Plain path string
        if isinstance(video, str) and os.path.exists(video):
            return ({"video_path": video, "type": "path"},)

        # Dict with path
        if isinstance(video, dict):
            if "path" in video and os.path.exists(video["path"]):
                return ({"video_path": video["path"], "type": "path"},)
            if "video_path" in video:
                return (video, )

        raise ValueError(f"Cannot resolve video from type: {type(video)}")


# ─────────────── AUDIO INPUT NODE ──────────────────────────────────────────
class SyncAudioInputNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "audio":      ("AUDIO",),
                "audio_path": ("STRING", {"default": ""}),
                "audio_url":  ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("SYNC_AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "provide_audio"
    CATEGORY = "Sync.so/Lipsync"

    def provide_audio(self, audio=None, audio_path="", audio_url=""):
        if audio is not None:
            return self._resolve_audio(audio)

        if audio_path and os.path.exists(audio_path):
            return ({"audio_path": audio_path, "type": "path"},)

        if audio_url:
            return ({"audio_url": audio_url, "type": "url"},)

        return ({"audio_path": "", "type": "path"},)

    def _resolve_audio(self, audio):
        # New ComfyUI API: AudioFromFile has save_to()
        if hasattr(audio, 'save_to'):
            tmpdir = tempfile.mkdtemp()
            temp_path = os.path.join(tmpdir, "input_audio.wav")
            audio.save_to(temp_path)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                return ({"audio_path": temp_path, "type": "path"},)
            raise ValueError("Failed to save AudioFromFile")

        # Dict format (waveform/sample_rate)
        if isinstance(audio, dict) and ("waveform" in audio or "audio" in audio):
            import numpy as np, soundfile as sf
            tmpdir = tempfile.mkdtemp()
            temp_path = os.path.join(tmpdir, "input_audio.wav")
            wv = audio.get("waveform", audio.get("audio"))
            sr = audio.get("sample_rate", 44100)
            wv_np = wv.squeeze(0).cpu().numpy() if hasattr(wv, 'cpu') else wv
            if len(wv_np.shape) == 2:
                wv_np = wv_np.mean(axis=0) if wv_np.shape[0] > 1 else wv_np[0]
            sf.write(temp_path, wv_np, sr)
            return ({"audio_path": temp_path, "type": "path"},)

        if isinstance(audio, str) and os.path.exists(audio):
            return ({"audio_path": audio, "type": "path"},)

        raise ValueError(f"Cannot resolve audio from type: {type(audio)}")


# ─────────────── GENERATE NODE ─────────────────────────────────────────────
class SyncLipsyncMainNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key":            ("SYNC_API_KEY", {"forceInput": True}),
                "video":              ("SYNC_VIDEO",   {"forceInput": True}),
                "audio":              ("SYNC_AUDIO",   {"forceInput": True}),
                "model":              (["lipsync-2-pro", "lipsync-2", "lipsync-1.9.0-beta"],),
                "sync_mode":          (["cut_off", "loop", "bounce", "silence", "remap"], {"default": "cut_off"}),
                "temperature":        ("FLOAT",   {"default": 0.5, "min": 0.0, "max": 1.0}),
                "active_speaker":     ("BOOLEAN", {"default": False}),
                "occlusion_detection":("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES  = ("VIDEO",)
    RETURN_NAMES  = ("video",)
    FUNCTION      = "lipsync_generate"
    CATEGORY      = "Sync.so/Lipsync"

    def lipsync_generate(self, api_key, video, audio, model, sync_mode,
                         temperature, active_speaker, occlusion_detection):

        api_key_str   = api_key["api_key"]
        headers       = {"x-api-key": api_key_str, "x-sync-source": "comfyui"}
        MAX_BYTES     = 20 * 1024 * 1024

        video_path = video.get("video_path", "")
        video_url  = video.get("video_url",  "")
        audio_path = audio.get("audio_path", "")
        audio_url  = audio.get("audio_url",  "")

        # ── Submit ────────────────────────────────────────────────────────
        # Build options (camelCase keys to match SDK/API)
        options = {
            "syncMode": sync_mode,
            "temperature": temperature,
        }
        if active_speaker:
            options["activeSpeakerDetection"] = {"autoDetect": True}
        if occlusion_detection:
            options["occlusionDetectionEnabled"] = True

        # Determine if we upload files or use URLs
        files = {}
        has_video_file = video_path and Path(video_path).exists()
        has_audio_file = audio_path and Path(audio_path).exists()

        if has_video_file:
            files["video"] = open(video_path, "rb")
        if has_audio_file:
            files["audio"] = open(audio_path, "rb")

        # Build form fields — omit `input` when uploading files (per SDK)
        fields = [("model", model)]

        if options:
            fields.append(("options", json.dumps(options)))

        # Only provide `input` array for URL-based inputs (not for file uploads)
        if not has_video_file and video_url:
            input_block = [{"type": "video", "url": video_url}]
            if not has_audio_file and audio_url:
                input_block.append({"type": "audio", "url": audio_url})
            fields.append(("input", json.dumps(input_block)))
        elif not has_audio_file and audio_url:
            input_block = [{"type": "audio", "url": audio_url}]
            fields.append(("input", json.dumps(input_block)))

        print(f"[Sync] Submitting job — model={model}")
        print(f"[Sync] Fields: {[(k, v[:80] if isinstance(v, str) else v) for k, v in fields]}")
        print(f"[Sync] Files: {list(files.keys())}")
        res = requests.post("https://api.sync.so/v2/generate",
                            headers=headers, data=fields, files=files or None)
        for f in files.values():
            f.close()

        print(f"[Sync] Response {res.status_code}: {res.text[:500]}")
        if res.status_code not in (200, 201):
            raise RuntimeError(f"sync.so error {res.status_code}: {res.text[:400]}")

        job_id = res.json()["id"]
        print(f"[Sync] Job ID: {job_id}")

        # ── Poll ──────────────────────────────────────────────────────────
        status = None
        poll_res = None
        while status not in {"COMPLETED", "FAILED"}:
            time.sleep(5)
            poll_res = requests.get(f"https://api.sync.so/v2/generate/{job_id}",
                                    headers=headers)
            poll_res.raise_for_status()
            status = poll_res.json()["status"]
            print(f"[Sync] Status: {status}")

        if status != "COMPLETED":
            raise RuntimeError(f"sync.so job failed: {status}")

        # ── Download ──────────────────────────────────────────────────────
        result      = poll_res.json()
        output_url  = result.get("outputUrl") or (result.get("result") or {}).get("outputUrl")
        if not output_url:
            raise RuntimeError("sync.so: no outputUrl in response")

        try:
            import folder_paths
            out_dir = folder_paths.get_output_directory()
        except Exception:
            out_dir = tempfile.mkdtemp()

        out_path = os.path.join(out_dir, f"sync_{job_id}.mp4")
        r = requests.get(output_url)
        r.raise_for_status()
        Path(out_path).write_bytes(r.content)
        print(f"[Sync] Saved → {out_path}")

        # ── Return as VIDEO type ──────────────────────────────────────────
        # Try new ComfyUI API VideoFromFile first
        try:
            from comfy_api.latest._input_impl.video_types import VideoFromFile
            return (VideoFromFile(out_path),)
        except ImportError:
            pass

        # Fallback: return path string (older ComfyUI)
        return (out_path,)


# ────────────── REGISTER ──────────────────────────────────────────────────
NODE_CLASS_MAPPINGS = {
    "SyncApiKeyNode":      SyncApiKeyNode,
    "SyncVideoInputNode":  SyncVideoInputNode,
    "SyncAudioInputNode":  SyncAudioInputNode,
    "SyncLipsyncMainNode": SyncLipsyncMainNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SyncApiKeyNode":      "sync.so – API Key",
    "SyncVideoInputNode":  "sync.so – Video Input",
    "SyncAudioInputNode":  "sync.so – Audio Input",
    "SyncLipsyncMainNode": "sync.so – Lipsync Generate",
}

print("[Sync.so] Nodes loaded.")
