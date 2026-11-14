"""Tickets de soporte: desarrollo, problemas, consultas u otros temas, de todas las empresas y grupos.

Viven en una base de datos CENTRAL (alias «tickets», ver erp/empresas.py · EmpresaRouter), la misma para todas las
instalaciones de Ceiba ERP, para que la plataforma de Ceiba los revise en un solo lugar. Por eso no tienen claves
foráneas a usuarios ni empresas (están en otras bases): guardan de dónde viene cada ticket como texto (instalación,
grupo, empresa y usuario).
"""
from django.db import models


class Ticket(models.Model):
    TIPOS = [('PROBLEMA', 'Problema o error'), ('DESARROLLO', 'Desarrollo o mejora'), ('CONSULTA', 'Consulta'),
             ('OTRO', 'Otro tema')]
    PRIORIDADES = [('BAJA', 'Baja'), ('MEDIA', 'Media'), ('ALTA', 'Alta'), ('URGENTE', 'Urgente')]
    ESTADOS = [('ABIERTO', 'Abierto'), ('REVISION', 'En revisión'), ('PROGRESO', 'En desarrollo'),
               ('ESPERA', 'Esperando su respuesta'), ('RESUELTO', 'Resuelto'), ('CERRADO', 'Cerrado')]
    ABIERTOS = ('ABIERTO', 'REVISION', 'PROGRESO', 'ESPERA')

    tipo = models.CharField(max_length=10, choices=TIPOS, default='PROBLEMA')
    prioridad = models.CharField(max_length=8, choices=PRIORIDADES, default='MEDIA')
    estado = models.CharField(max_length=8, choices=ESTADOS, default='ABIERTO', db_index=True)
    titulo = models.CharField('Asunto', max_length=150)
    descripcion = models.TextField('Descripción')
    modulo = models.CharField('Módulo', max_length=40, blank=True)
    url = models.CharField('Página', max_length=500, blank=True)
    navegador = models.CharField(max_length=300, blank=True)
    # de dónde viene: instalación de Ceiba ERP, grupo (empresa principal), empresa y usuario
    instalacion = models.CharField('Instalación', max_length=60, db_index=True)
    dominio = models.CharField(max_length=120, blank=True)
    version_erp = models.CharField('Versión del ERP', max_length=20, blank=True)
    grupo_ruc = models.CharField('RUC del grupo', max_length=11, blank=True)
    grupo_nombre = models.CharField('Grupo', max_length=200, blank=True)
    empresa_alias = models.CharField(max_length=30, blank=True)
    empresa_ruc = models.CharField('RUC de la empresa', max_length=11, blank=True, db_index=True)
    empresa_nombre = models.CharField('Empresa', max_length=200, blank=True)
    usuario = models.CharField(max_length=150, db_index=True)
    usuario_nombre = models.CharField(max_length=150, blank=True)
    usuario_correo = models.CharField(max_length=254, blank=True)
    # lo usa la plataforma de revisión de Ceiba
    asignado_a = models.CharField(max_length=150, blank=True)
    creado = models.DateTimeField(auto_now_add=True, db_index=True)
    actualizado = models.DateTimeField(auto_now=True)
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-actualizado', '-id']
        indexes = [models.Index(fields=['instalacion', 'empresa_alias', 'estado'])]
        verbose_name = 'ticket'

    def __str__(self):
        return f'{self.codigo} · {self.titulo}'

    @property
    def codigo(self):
        return f'TK-{self.pk:06d}' if self.pk else 'TK-nuevo'

    @property
    def abierto(self):
        return self.estado in self.ABIERTOS


class Comentario(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comentarios')
    autor = models.CharField(max_length=150)
    autor_nombre = models.CharField(max_length=150, blank=True)
    de_soporte = models.BooleanField('Respuesta de soporte', default=False)
    texto = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado', 'id']


class Adjunto(models.Model):
    """Archivo del ticket (captura, PDF, Excel…): se guarda en la base central para que la plataforma lo vea."""
    MAXIMO = 5 * 1024 * 1024
    EXTENSIONES = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'txt', 'csv', 'log', 'xlsx', 'xls', 'docx', 'doc',
                   'zip', 'xml', 'json'}
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='adjuntos')
    comentario = models.ForeignKey(Comentario, on_delete=models.CASCADE, null=True, blank=True,
                                   related_name='adjuntos')
    nombre = models.CharField(max_length=200)
    tipo = models.CharField(max_length=100, blank=True)
    tamano = models.PositiveIntegerField(default=0)
    contenido = models.BinaryField()
    subido_por = models.CharField(max_length=150, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['creado', 'id']
