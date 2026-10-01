from pydantic import BaseModel, ConfigDict


class OronderBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
            else:
                result[key] = value
        return result
