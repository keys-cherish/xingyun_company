import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { Button, Card, Cell, CellGroup, Empty, NavBar, PullRefresh, Skeleton, Tag } from 'vant';

import App from './App.vue';
import 'vant/lib/index.css';

const app = createApp(App);
app.use(createPinia());
app.use(Button);
app.use(Card);
app.use(Cell);
app.use(CellGroup);
app.use(Empty);
app.use(NavBar);
app.use(PullRefresh);
app.use(Skeleton);
app.use(Tag);
app.mount('#app');
