from .empresas import parchar_transacciones

parchar_transacciones()  # antes de importar las apps: los @transaction.atomic usan la base de la empresa activa
