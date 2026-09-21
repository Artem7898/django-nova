from types import SimpleNamespace

from django.db import models


class Profile(models.Model):
    label = models.CharField(max_length=50)


class Author(models.Model):
    name = models.CharField(max_length=50)
    profile = models.OneToOneField(Profile, null=True, on_delete=models.SET_NULL)


class Tag(models.Model):
    name = models.CharField(max_length=50)


class Article(models.Model):
    title = models.CharField(max_length=50)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="articles")
    tags = models.ManyToManyField(Tag, related_name="articles")
    _nova_config = SimpleNamespace(cache_enabled=True)


class Comment(models.Model):
    text = models.CharField(max_length=50)
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="comments")
