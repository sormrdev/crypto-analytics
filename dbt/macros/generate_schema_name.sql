{#
    dbt's default would put `+schema: reporting` into `public_reporting`.
    Use the configured schema name as-is, so Power BI keeps reading `reporting.*`.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
