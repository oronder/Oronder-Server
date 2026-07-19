from pydantic import BaseModel


class OronderBaseModel(BaseModel):
    def to_dict(self):
        result = {}
        for key, value in vars(self).items():
            if isinstance(value, OronderBaseModel):
                result[key] = value.to_dict()
            elif isinstance(value, list):
                result[key] = [
                    item.to_dict() if isinstance(item, OronderBaseModel) else item
                    for item in value
                ]
            elif isinstance(value, dict):
                result[key] = {
                    k: v.to_dict() if isinstance(v, OronderBaseModel) else v
                    for k, v in value.items()
                }
            else:
                result[key] = value
        return result

    class Config:
        from_attributes = True
