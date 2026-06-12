"""
cache_model.py
Pre-downloads and caches the Ultravox model during Docker build.
Matches the monkey-patch suite used in chat.py.
"""

import transformers
import transformers.modeling_utils

# 1. Monkey-patch _init_weights to True to let Ultravox instantiate the language model
if not hasattr(transformers.modeling_utils, "_init_weights"):
    transformers.modeling_utils._init_weights = True

# 2. Monkey-patch check_and_set_device_map to bypass meta device context check errors
try:
    transformers.modeling_utils.check_and_set_device_map = lambda x: x
except Exception:
    pass
try:
    import transformers.integrations.accelerate
    transformers.integrations.accelerate.check_and_set_device_map = lambda x: x
except Exception:
    pass

# 3. Monkey-patch PreTrainedModel._initialize_weights to bypass meta tensor copy/fill errors during initialization
try:
    original_initialize_weights = transformers.modeling_utils.PreTrainedModel._initialize_weights
    def safe_initialize_weights(self, module, *args, **kwargs):
        try:
            return original_initialize_weights(self, module, *args, **kwargs)
        except (NotImplementedError, RuntimeError) as e:
            err_msg = str(e)
            is_meta = "meta tensor" in err_msg or "meta device" in err_msg
            if not is_meta:
                try:
                    is_meta = any(p.device.type == 'meta' for p in module.parameters()) or any(b.device.type == 'meta' for b in module.buffers())
                except Exception:
                    pass
            if is_meta:
                pass
            else:
                raise
    transformers.modeling_utils.PreTrainedModel._initialize_weights = safe_initialize_weights
except Exception:
    pass

# 4. Monkey-patch PreTrainedModel.init_weights to wrap the instance's tie_weights method dynamically.
try:
    original_init_weights = transformers.modeling_utils.PreTrainedModel.init_weights
    def safe_init_weights(self, *args, **kwargs):
        if hasattr(self, "tie_weights"):
            original_tie_weights = self.tie_weights
            def wrapped_tie_weights(*args, **kwargs):
                try:
                    return original_tie_weights()
                except TypeError:
                    return original_tie_weights(*args, **kwargs)
            self.tie_weights = wrapped_tie_weights
        return original_init_weights(self, *args, **kwargs)
    transformers.modeling_utils.PreTrainedModel.init_weights = safe_init_weights
except Exception:
    pass

print("[Cache] Pre-downloading and caching fixie-ai/ultravox-v0_6-llama-3_1-8b model...")
# Cache using pipeline loading on CPU
transformers.pipeline(
    model='fixie-ai/ultravox-v0_6-llama-3_1-8b',
    trust_remote_code=True,
    device="cpu"
)
print("[Cache] Model pre-download completed successfully!")
