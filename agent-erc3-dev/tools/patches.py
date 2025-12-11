"""
Runtime patches for SDK models.

Fixes issues with the erc3 SDK where optional fields are incorrectly
marked as required.
"""
from typing import List, Optional

from erc3.erc3 import client


def _patch_update_employee_model(model_class, class_name: str) -> bool:
    """
    Patch a Req_UpdateEmployeeInfo model to make fields Optional.

    The erc3 library enforces non-optional lists for skills/wills,
    causing empty lists to be sent even when only updating salary.

    Args:
        model_class: The model class to patch
        class_name: Name for logging

    Returns:
        True if patch successful
    """
    try:
        if hasattr(model_class, 'model_fields'):
            # Pydantic v2
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.model_fields:
                    model_class.model_fields[field].default = None
                    # For list types, make them Optional
                    if field in ['skills', 'wills']:
                        from erc3.erc3 import dtos
                        model_class.model_fields[field].annotation = Optional[List[dtos.SkillLevel]]
                    else:
                        model_class.model_fields[field].annotation = Optional[str]
            # Rebuild model
            if hasattr(model_class, 'model_rebuild'):
                model_class.model_rebuild()
        else:
            # Pydantic v1
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.__fields__:
                    model_class.__fields__[field].required = False
                    model_class.__fields__[field].default = None

        print(f"🔧 Patched {class_name} to support optional fields.")
        return True
    except Exception as e:
        print(f"⚠️ Failed to patch {class_name}: {e}")
        return False


# Apply patches at module load time
try:
    from erc3.erc3 import dtos
    _patch_update_employee_model(dtos.Req_UpdateEmployeeInfo, "dtos.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch dtos.Req_UpdateEmployeeInfo: {e}")

try:
    _patch_update_employee_model(client.Req_UpdateEmployeeInfo, "client.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch client.Req_UpdateEmployeeInfo: {e}")


class SafeReq_UpdateEmployeeInfo(client.Req_UpdateEmployeeInfo):
    """
    Safe wrapper for Req_UpdateEmployeeInfo.

    Ensures we don't send null values for optional fields,
    which would overwrite existing data with nulls/defaults.
    """

    def model_dump(self, **kwargs):
        """Dump model excluding None and empty values."""
        kwargs['exclude_none'] = True
        data = super().model_dump(**kwargs)
        # Remove empty lists/strings for optional fields
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data

    def dict(self, **kwargs):
        """Fallback for Pydantic v1 or older usage."""
        kwargs['exclude_none'] = True
        data = super().dict(**kwargs)
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data

    def model_dump_json(self, **kwargs):
        """JSON serialization excluding None and empty values."""
        import json
        data = self.model_dump(**kwargs)
        return json.dumps(data)
