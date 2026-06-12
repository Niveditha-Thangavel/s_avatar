"""
cache_model.py
Pre-downloads and caches the Ultravox model during Docker build.
Matches the monkey-patch suite used in chat.py.
"""

import transformers
import transformers.modeling_utils
import transformers.processing_utils

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
            if "Ultravox" in self.__class__.__name__:
                err_msg = str(e)
                is_meta = "meta tensor" in err_msg or "meta device" in err_msg
                if not is_meta:
                    try:
                        is_meta = any(p.device.type == 'meta' for p in module.parameters()) or any(b.device.type == 'meta' for b in module.buffers())
                    except Exception:
                        pass
                if is_meta:
                    return
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

# 5. Monkey-patch PreTrainedModel.from_pretrained to normalize device_map if it's not a dictionary
try:
    original_from_pretrained = transformers.modeling_utils.PreTrainedModel.from_pretrained
    @classmethod
    def safe_from_pretrained(cls, *args, **kwargs):
        device_map = kwargs.get("device_map", None)
        if device_map is not None and not hasattr(device_map, "values"):
            kwargs["device_map"] = {"": device_map}
        return original_from_pretrained.__func__(cls, *args, **kwargs)
    transformers.modeling_utils.PreTrainedModel.from_pretrained = safe_from_pretrained
except Exception:
    pass

# 6. Monkey-patch PreTrainedModel._load_pretrained_model to normalize device_map if it's not a dictionary
try:
    original_load_pretrained_model = transformers.modeling_utils.PreTrainedModel._load_pretrained_model
    def safe_load_pretrained_model(*args, **kwargs):
        # Scan all arguments to locate any config object featuring a device_map attribute
        for arg in list(args) + list(kwargs.values()):
            if hasattr(arg, "device_map") and arg.device_map is not None:
                if not hasattr(arg.device_map, "values"):
                    object.__setattr__(arg, 'device_map', {"": arg.device_map})
        
        result = original_load_pretrained_model(*args, **kwargs)
        
        # Locate the model instance
        model = kwargs.get("model", None)
        if model is None and len(args) > 0:
            model = args[0]
            
        if model is not None:
            # Find the non-meta device of the model if any
            target_device = "cpu"
            for p in model.parameters():
                if p.device.type != "meta":
                    target_device = p.device
                    break
            
            # Replace any remaining meta parameters and buffers
            for name, module in model.named_modules():
                for param_name, param in list(module.named_parameters(recurse=False)):
                    if param.device.type == "meta":
                        new_param = torch.nn.Parameter(torch.empty_like(param, device=target_device))
                        torch.nn.init.zeros_(new_param)
                        setattr(module, param_name, new_param)
                for buf_name, buf in list(module.named_buffers(recurse=False)):
                    if buf.device.type == "meta":
                        new_buf = torch.empty_like(buf, device=target_device)
                        torch.nn.init.zeros_(new_buf)
                        setattr(module, buf_name, new_buf)
        return result
    transformers.modeling_utils.PreTrainedModel._load_pretrained_model = safe_load_pretrained_model
except Exception:
    pass

# 7. Monkey-patch ProcessorMixin.check_argument_for_proper_class to bypass WhisperProcessor type validation check
try:
    original_check_arg = transformers.processing_utils.ProcessorMixin.check_argument_for_proper_class
    def safe_check_argument_for_proper_class(self, argument_name, argument):
        try:
            return original_check_arg(self, argument_name, argument)
        except TypeError:
            if argument_name == "audio_processor" and argument.__class__.__name__ == "WhisperProcessor":
                return argument.__class__
            raise
    transformers.processing_utils.ProcessorMixin.check_argument_for_proper_class = safe_check_argument_for_proper_class
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
