import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { applyMode, savedMode } from './theme.js'

applyMode(savedMode())

export default mount(App, { target: document.getElementById('app') })
