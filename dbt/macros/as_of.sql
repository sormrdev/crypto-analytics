{#
    "Now" for every time window in the project. Unit tests override this macro with a fixed
    timestamp, so models that depend on the current time can still be tested exactly.
#}
{% macro as_of() -%}
    now()
{%- endmacro %}
