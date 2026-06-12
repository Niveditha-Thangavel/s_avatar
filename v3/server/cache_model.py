import transformers.modeling_utils
import transformers.integrations.accelerate

# Monkey-patch _init_weights to True to let Ultravox instantiate the language model
transformers.modeling_utils._init_weights = True

# Monkey-patch check_and_set_device_map to bypass meta device context check errors
try:
    transformers.modeling_utils.check_and_set_device_map = lambda x: x
except Exception:
    pass
try:
    transformers.integrations.accelerate.check_and_set_device_map = lambda x: x
except Exception:
    pass

# Monkey-patch PreTrainedModel._initialize_weights to bypass meta tensor copy/fill errors during initialization
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

import transformers
print("[Cache] Pre-downloading and caching fixie-ai/ultravox-v0_6-llama-3_1-8b model...")
transformers.pipeline(model='fixie-ai/ultravox-v0_6-llama-3_1-8b', trust_remote_code=True)
print("[Cache] Model pre-download completed successfully!")
