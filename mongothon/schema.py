from datetime import datetime
from types import FunctionType
from inspect import getargspec



class Mixed(object):
    """Mixed type, used to indicate a field in a schema can be
    one of many types. Use as a last resort only.
    The Mixed type can be used directly as a class to indicate
    any type is permitted for a given field:
    `"my_field": {"type": Mixed}`
    It can also be instantiated with list of specific types the 
    field may is allowed to be for more control:
    `"my_field": {"type": Mixed(ObjectId, int)}`
    """
    def __init__(self, *types):
        if len(types) < 2:
            raise ValueError("Mixed type requires at least 2 specific types")
        for mtype in types:
            if mtype not in SUPPORTED_TYPES:
                raise ValueError("{0} is not a supported type.".format(mtype))        
        self.types = set(types)

    def is_instance_of_enclosed_type(self, value):
        """Returns true of the given value is an instance of 
        one of the types enclosed by this mixed type instance."""
        for mtype in self.types:
            if isinstance(value, mtype):
                return True
        return False

SUPPORTED_TYPES = [basestring, int, float, datetime, long, bool, Mixed]

def _append_path(prefix, field):
    """Appends the given field to the given path prefix."""
    if prefix:
        return "{0}.{1}".format(prefix, field)
    else:
        return field

def _verify_schema(schema, path_prefix=None):
    """Verifies that the given schema is valid. This method is recursive and verifies and
    schemas nested within the given schema."""
    for field, spec in schema.doc_spec.iteritems():
        path = _append_path(path_prefix, field)
        
        # Standard dict-based spec
        if isinstance(spec, dict):
            _verify_field_spec(spec, path)
            
        # An embedded collection
        elif isinstance(spec, list):
            if len(spec) == 0 or len(spec) > 1:
                raise SchemaFormatException(
                    "Exactly one type must be declared for the embedded collection at {0}",
                    path)

            # If the list type is a schema, recurse into it
            if isinstance(spec[0], Schema):
                _verify_schema(spec[0], path)
                continue
                
            # Otherwise just make sure it's supported
            if spec[0] not in SUPPORTED_TYPES:
                raise SchemaFormatException(
                    "Embedded collection at {0} not described using a Schema object.",
                    path)


        else:
            raise SchemaFormatException("Invalid field definition for {0}", path)


def _verify_field_spec(spec, path):
    """Verifies a given field specification is valid, recursing into nested schemas if required."""

    # Required should be a boolean
    if 'required' in spec and not isinstance(spec['required'], bool):
        raise SchemaFormatException("{0} required declaration should be True or False", path)

    # Must have a type specified
    if 'type' not in spec:
        raise SchemaFormatException("{0} has no type declared.", path)

    field_type = spec['type']

    if isinstance(field_type, Schema):
        # Nested documents cannot have defaults or validation
        if not set(spec.keys()).issubset(set(['type', 'required'])):
            raise SchemaFormatException("Unsupported field spec item at {0}. Items: "+repr(spec.keys()), path)
        
        # Recurse into nested Schema
        _verify_schema(field_type, path)
        return

    # Must be one of the supported types
    if field_type not in SUPPORTED_TYPES and not isinstance(field_type, Mixed):
        raise SchemaFormatException("{0} is not declared with a valid type.", path)
    
    # Validations should be either a single function or array of functions
    if 'validates' in spec:
        validates = spec['validates']

        if isinstance(validates, list): 
            for validator in validates:
                _verify_validator(validator, path)
        else:
            _verify_validator(validates, path)

    # Defaults must be of the correct type or a function
    if 'default' in spec and not (isinstance(spec['default'], field_type) or isinstance(spec['default'], FunctionType)):
        raise SchemaFormatException("Default value for {0} is not of the nominated type.", path)

    # Only expected spec keys are supported
    if not set(spec.keys()).issubset(set(['type', 'required', 'validates', 'default'])):
        raise SchemaFormatException("Unsupported field spec item at {0}. Items: "+repr(spec.keys()), path)


def _verify_validator(validator, path):
    """Verifies that a given validator associated with the field at the given path is legitimate."""

    # Validator should be a function
    if not isinstance(validator, FunctionType):
        raise SchemaFormatException("Invalid validations for {0}", path)

    # Validator should accept a single argument
    (args, varargs, keywords, defaults) = getargspec(validator)
    if len(args) != 1:
        raise SchemaFormatException("Invalid validations for {0}", path)


def _validate_instance_against_schema(instance, schema, errors, path_prefix=''):
    """Validates that the given instance of a document conforms to the given schema's
    structure and validations. Any validation errors are added to the given errors 
    collection. The caller should assume the instance is considered valid if the 
    errors collection is empty when this method returns."""

    if not isinstance(instance, dict):
        errors[path_prefix] = "Expected instance of dict to validate against schema."
        return

    # Loop over each field in the schema and check the instance value conforms
    # to its spec
    for field, spec in schema.doc_spec.iteritems():
        value = instance.get(field, None)

        path = _append_path(path_prefix, field)
        
        # Standard dict-based spec
        if isinstance(spec, dict):
            _validate_value(value, spec, path, errors)

        # An embedded collection
        elif isinstance(spec, list):
            if (value is not None):
                if not isinstance(value, list):
                    errors[path] = "Expected a list."
                    continue
                else:
                    for i, item in enumerate(value):
                        instance_path = "{0}.{1}".format(path, i)

                        if isinstance(spec[0], Schema):
                            _validate_instance_against_schema(item, spec[0], errors, instance_path)
                        elif not isinstance(item, spec[0]):
                            errors[instance_path] = "List item is of incorrect type"


def _validate_value(value, field_spec, path, errors):
    """Validates that the given field value is valid given the associated 
    field spec and path. Any validation failures are added to the given errors
    collection."""

    # Check for an empty value and bail out if necessary applying the required
    # constraint in the process.
    if value is None:
        if field_spec.get('required', False):
            errors[path] = "%s is required." % path
        return

    # All fields should have a type
    field_type = field_spec['type']

    # If our field is an embedded document, recurse into it
    if isinstance(field_type, Schema):
        if isinstance(value, dict):
            _validate_instance_against_schema(value, field_type, errors, path)
        else:
            errors[path] = "%s should be an embedded document" % path
        return

    # Otherwise, validate the field - mixed fields are handled
    # slightly differently
    if isinstance(field_type, Mixed):
        if not field_type.is_instance_of_enclosed_type(value):
            errors[path] = "Field should be one of the types specified."
            return
    elif field_type is not Mixed and not isinstance(value, field_type):
        errors[path] = "Field should be of type {0}".format(field_type)
        return

    validations = field_spec.get('validates', None)
    if validations is None:
        return

    def apply(fn):
        error = fn(value)
        if error:
            errors[path] = error

    if isinstance(validations, list):
        for validation in validations:
            apply(validation)
    else:
        apply(validations)


def _apply_schema_defaults(schema, instance):
    """Applies the defaults described by the given schema to the given
    document instance as appropriate. Defaults are only applied to 
    fields which are currently unset."""

    for field, spec in schema.doc_spec.iteritems():

        # Determine if a value already exists for the field
        if field in instance:
            value = instance[field]

            # recurse into nested collections
            if isinstance(spec, list):
                if isinstance(value, list) and isinstance(spec[0], Schema):
                    for item in value:
                        _apply_schema_defaults(spec[0], item)
              
            # recurse into nested docs
            elif isinstance(spec['type'], Schema) and isinstance(value, dict):
                _apply_schema_defaults(spec['type'], value)

            # Bailout as we don't want to apply a default
            continue

        # Apply a default if one is available
        if isinstance(spec, dict) and 'default' in spec:
            default = spec['default']
            if (isinstance(default, FunctionType)):
                instance[field] = default()
            else:
                instance[field] = default



class SchemaFormatException(Exception):
    """Exception which encapsulates a problem found during the verification of a
    a schema."""

    def __init__(self, message, path):
        self._message = message.format(path)
        self._path = path

    @property
    def path(self):
        """The field path at which the format error was found."""
        return self._path

    def __str__(self):
        return self._message


class ValidationException(Exception):
    """Exception which is thrown in response to the failed validation of a document
    against it's associated schema."""

    def __init__(self, errors):
        self._errors = errors

    @property
    def errors(self):
        """A dict containing the validation error(s) found at each field path."""
        return self._errors

    def __str__(self):
        return repr(self._errors)


class VirtualField(object):
    """Encapsulates a named virtual field associated with a Schema,
    with methods to register and apply getters and setters."""

    def get(self, fn):
        """Registers a getter function with this virtual field. The getter 
        function should expect to receive a document structure which matches the
        enclosing schema and should return the value of the virtual field
        derived from this document."""
        (args, varargs, keywords, defaults) = getargspec(fn)
        if len(args) != 1:
            raise ValueError('Virtual field getters take 1 argument only')

        self._getter = fn

    def set(self, fn):
        """Register a setter function with this virtual field. The setter 
        should expect to be passed the value being set and the document to 
        which it suould be applied."""
        (args, varargs, keywords, defaults) = getargspec(fn)
        if len(args) != 2:
            raise ValueError('Virtual field getters take 2 arguments only')

        self._setter = fn

    def has_getter(self):
        """Returns true if the given virtual field has an associated getter function."""
        return self._getter != None

    def has_setter(self):
        """Returns true if the given virtual field has an associated setter function."""
        return self._setter != None

    def on_get(self, doc):
        """Applies the registered getter function to the given document using 
        the and return the calculated value."""
        return self._getter(doc)

    def on_set(self, value, doc):
        """Applies the given value to the given document using the registered
        setter."""
        return self._setter(value, doc)


class Schema(object):
    """A Schema encapsulates the structure and constraints of a Mongo document."""

    def __init__(self, doc_spec):
        self._doc_spec = doc_spec
        self._virtuals = {}

    @property
    def doc_spec(self):
        return self._doc_spec

    def verify(self):
        """Verifies that the given schema document spec is valid."""
        _verify_schema(self)

    def apply_defaults(self, instance):
        """Applies default values to the given document"""
        _apply_schema_defaults(self, instance)

    def validate(self, instance):
        """Validates the given document against this schema. Raises a 
        ValidationException if there are any failures."""
        errors = {}
        _validate_instance_against_schema(instance, self, errors)

        if len(errors) > 0:
            raise ValidationException(errors)

    def virtual(self, field_name, getter=None, setter=None):
        """Allows a virtual field definition to be provided."""
        if not field_name in self._virtuals:
            self._virtuals[field_name] = VirtualField()

        virtual_field = self._virtuals[field_name]

        if getter:
            virtual_field.get(getter)
        if setter:
            virtual_field.set(setter)

        return virtual_field

    @property
    def virtuals(self):
        return self._virtuals
