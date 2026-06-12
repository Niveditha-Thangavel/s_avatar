import sys

# MUST import and patch dynamic_module_utils BEFORE importing other transformers modules,
# so that any auto-factories import the patched version directly.
import transformers.dynamic_module_utils
original_get_class_from_dynamic_module = transformers.dynamic_module_utils.get_class_from_dynamic_module

def custom_get_class_from_dynamic_module(*args, **kwargs):
    cls = original_get_class_from_dynamic_module(*args, **kwargs)
    if cls and hasattr(cls, "tie_weights") and "Ultravox" in cls.__name__ and not hasattr(cls, "_tie_weights_patched"):
        original_tie_weights = cls.tie_weights
        def wrapped_tie_weights(self, *args, **kwargs):
            try:
                return original_tie_weights(self)
            except Exception as e:
                raise e
        cls.tie_weights = wrapped_tie_weights
        cls._tie_weights_patched = True
    return cls

transformers.dynamic_module_utils.get_class_from_dynamic_module = custom_get_class_from_dynamic_module

# Apply the patch to all potential auto classes in transformers immediately
# This handles any lazy loading mechanisms safely
modules_to_patch = [
    "transformers.dynamic_module_utils",
    "transformers.models.auto.auto_factory",
    "transformers.models.auto.modeling_auto",
    "transformers.models.auto.tokenization_auto",
    "transformers.models.auto.processing_auto",
    "transformers.models.auto.configuration_auto",
]

for module_name in modules_to_patch:
    try:
        mod = __import__(module_name, fromlist=["get_class_from_dynamic_module"])
        if hasattr(mod, "get_class_from_dynamic_module"):
            setattr(mod, "get_class_from_dynamic_module", custom_get_class_from_dynamic_module)
    except Exception:
        pass

# Now import the rest of the modules safely
import transformers.modeling_utils
import transformers.integrations.accelerate

# 1. Monkey-patch _init_weights to True to let Ultravox instantiate the language model
transformers.modeling_utils._init_weights = True

# 2. Monkey-patch check_and_set_device_map to bypass meta device context check errors
try:
    transformers.modeling_utils.check_and_set_device_map = lambda x: x
except Exception:
    pass
try:
    transformers.integrations.accelerate.check_and_set_device_map = lambda x: x
except Exception:
    pass

# 3. Monkey-patch PreTrainedModel._initialize_weights to bypass meta tensor copy/fill errors during initialization
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
