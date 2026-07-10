from django.contrib import admin

from .models import (
    Section, Table, KitchenStation, MenuModifierGroup, MenuModifier,
    Recipe, RecipeItem, Driver, CashCustody,
)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'is_active')
    list_filter = ('branch', 'is_active')


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ('number', 'branch', 'section', 'seats', 'status', 'is_active')
    list_filter = ('branch', 'section', 'status')
    search_fields = ('number',)


@admin.register(KitchenStation)
class KitchenStationAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'printer_target', 'is_active')
    list_filter = ('branch', 'is_active')


class MenuModifierInline(admin.TabularInline):
    model = MenuModifier
    extra = 1


@admin.register(MenuModifierGroup)
class MenuModifierGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_required', 'min_select', 'max_select', 'sort_order')
    filter_horizontal = ('products', 'categories')
    inlines = [MenuModifierInline]


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ('product', 'is_active', 'created_at')
    search_fields = ('product__name',)
    inlines = [RecipeItemInline]


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'phone', 'is_active')
    list_filter = ('branch', 'is_active')
    search_fields = ('name', 'phone')


@admin.register(CashCustody)
class CashCustodyAdmin(admin.ModelAdmin):
    list_display = ('holder_name', 'kind', 'branch', 'amount', 'status', 'created_at', 'settled_at')
    list_filter = ('branch', 'kind', 'status')
    search_fields = ('holder_name',)
