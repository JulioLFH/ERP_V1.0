# Formato Perú: punto decimal y coma de miles (el español de Django usa coma decimal, que mostraba 371,6343 en
# pantalla y hacía que el portal leyera mal los importes precargados). Las fechas siguen las del español.
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3
