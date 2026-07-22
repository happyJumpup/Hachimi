import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DesktopPet from '@/components/DesktopPet.vue'

function installBrowserStubs() {
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })))
}

async function dispatchPointer(
  wrapper: ReturnType<typeof mount>,
  eventName: string,
  values: { pointerId: number, clientX: number, clientY: number, button?: number },
) {
  const event = new MouseEvent(eventName, {
    bubbles: true,
    cancelable: true,
    button: values.button ?? 0,
    clientX: values.clientX,
    clientY: values.clientY,
  })
  Object.defineProperty(event, 'pointerId', { value: values.pointerId })
  wrapper.get('.desktop-pet').element.dispatchEvent(event)
  await wrapper.vm.$nextTick()
}

describe('DesktopPet', () => {
  beforeEach(() => {
    window.localStorage.clear()
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 768 })
    installBrowserStubs()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the requested action and switches its frame source', async () => {
    const wrapper = mount(DesktopPet, { props: { action: 'idle' } })

    expect(wrapper.get('.desktop-pet').attributes('data-action')).toBe('idle')
    expect(wrapper.get('img').attributes('src')).toContain('/idle/hotblood_idle_01.png')

    await wrapper.setProps({ action: 'cheer' })

    expect(wrapper.get('.desktop-pet').attributes('data-action')).toBe('cheer')
    expect(wrapper.get('img').attributes('src')).toContain('/cheer/hotblood_cheer_01.png')
  })

  it('switches characters and resolves each character drag action', async () => {
    const wrapper = mount(DesktopPet, {
      props: { petId: 'gentle', action: 'guide' },
    })
    const pet = wrapper.get('.desktop-pet')

    expect(pet.attributes('data-pet-id')).toBe('gentle')
    expect(pet.attributes('data-action')).toBe('guide')
    expect(wrapper.get('img').attributes('src')).toContain('/pets/gentle/frames/guide/gentle_guide_01.png')

    await dispatchPointer(wrapper, 'pointerdown', {
      pointerId: 11,
      clientX: 300,
      clientY: 600,
    })
    expect(pet.attributes('data-action')).toBe('rest')

    await dispatchPointer(wrapper, 'pointerup', {
      pointerId: 11,
      clientX: 300,
      clientY: 600,
    })
    await wrapper.setProps({ petId: 'analyst', action: 'scan' })

    expect(pet.attributes('data-pet-id')).toBe('analyst')
    expect(pet.attributes('data-action')).toBe('scan')
    expect(wrapper.get('img').attributes('src')).toContain('/pets/analyst/frames/scan/analyst_scan_01.png')
  })

  it('can be hidden without rendering a blocking overlay', () => {
    const wrapper = mount(DesktopPet, { props: { visible: false } })

    expect(wrapper.find('.desktop-pet').exists()).toBe(false)
  })

  it('uses drag while moving and persists the final position', async () => {
    const persistKey = 'test.hotblood.position'
    const wrapper = mount(DesktopPet, { props: { action: 'cheer', persistKey } })
    const pet = wrapper.get('.desktop-pet')
    await vi.waitFor(() => expect(pet.classes()).toContain('is-ready'))

    await dispatchPointer(wrapper, 'pointerdown', {
      button: 0,
      pointerId: 3,
      clientX: 300,
      clientY: 600,
    })
    expect(pet.attributes('data-action')).toBe('drag')

    await dispatchPointer(wrapper, 'pointermove', {
      pointerId: 3,
      clientX: 220,
      clientY: 500,
    })
    await dispatchPointer(wrapper, 'pointerup', {
      pointerId: 3,
      clientX: 220,
      clientY: 500,
    })

    expect(pet.attributes('data-action')).toBe('cheer')
    expect(JSON.parse(window.localStorage.getItem(persistKey)!)).toEqual({ x: 760, y: 460 })
  })

  it('restores a saved position and disappears without blocking on an asset error', async () => {
    const persistKey = 'test.hotblood.restore'
    window.localStorage.setItem(persistKey, JSON.stringify({ x: 44, y: 72 }))
    const wrapper = mount(DesktopPet, { props: { action: 'idle', persistKey } })

    await vi.waitFor(() => {
      expect(wrapper.get('.desktop-pet').attributes('style')).toContain('translate3d(44px, 72px, 0)')
    })

    await wrapper.get('img').trigger('error')

    expect(wrapper.find('.desktop-pet').exists()).toBe(false)
    expect(wrapper.emitted('asset-error')).toHaveLength(1)
  })
})
